import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import (
    FF_TOLERANCE,
    derive_ff,
    estimate_power,
    judge,
    paths_consistent,
)

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    now = datetime.now(timezone.utc)
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, 480.0, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, 520.0, "衰减"),
            ]
            for code, voc, isc, ff, rating, expect in samples:
                est = estimate_power(voc, isc)
                ff_derived = derive_ff(est, rating)
                assert paths_consistent(ff, ff_derived)
                verdict, reason = judge(ff)
                assert verdict == expect
                row = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, est_power_w, status,
                        verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)
                       RETURNING id""",
                    (code, voc, isc, ff, est, verdict, reason, now, now),
                ).fetchone()
                conn.execute(
                    """INSERT INTO check_traces
                       (scan_id, string_code, voc_v, isc_a, est_power_w, rating_w,
                        ff_submitted, ff_derived, consistent, created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,'scanner',%s)""",
                    (row["id"], code, voc, isc, est, rating, ff, ff_derived, now),
                )
        m = conn.execute("SELECT COUNT(*) AS n FROM nameplate_ratings").fetchone()["n"]
        if m == 0:
            for code, rating in [("阵列A-串03", 480.0), ("阵列B-串11", 520.0)]:
                conn.execute(
                    """INSERT INTO nameplate_ratings (string_code, rating_w, created_by, created_at)
                       VALUES (%s,%s,'scanner',%s)
                       ON CONFLICT (string_code) DO NOTHING""",
                    (code, rating, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可写，观察员只读")
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, est_power_w,
                      status, verdict, reason, created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")

    def opt_float(key):
        val = data.get(key)
        if val is None or val == "":
            return None
        try:
            return float(val)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{key} 必须是数字")

    voc = opt_float("voc_v")
    isc = opt_float("isc_a")
    ff = opt_float("fill_factor")
    if (voc is None) != (isc is None):
        raise HTTPException(status_code=400, detail="开路电压与短路电流必须一起交")
    if voc is None and ff is None:
        raise HTTPException(status_code=400, detail="交电压电流或直填填充因子，至少走一条路")
    if voc is not None and (voc <= 0 or isc <= 0):
        raise HTTPException(status_code=400, detail="开路电压与短路电流必须为正数")
    if ff is not None and ff <= 0:
        raise HTTPException(status_code=400, detail="填充因子必须为正数")

    now = datetime.now(timezone.utc)
    with connect() as conn:
        rating_row = conn.execute(
            "SELECT rating_w FROM nameplate_ratings WHERE string_code = %s", (code,)
        ).fetchone()
        rating_w = float(rating_row["rating_w"]) if rating_row else None
        est = ff_derived = None
        if voc is not None:
            if rating_w is None:
                raise HTTPException(
                    status_code=400,
                    detail="该组串尚未登记铭牌当量，无法对表，请先在铭牌功率页左侧填当量",
                )
            est = estimate_power(voc, isc)
            ff_derived = derive_ff(est, rating_w)
        if ff is not None and ff_derived is not None and not paths_consistent(ff, ff_derived):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"两条路对不上：直填填充因子 {ff}，按电压电流对表推算 "
                    f"{round(ff_derived, 4)}（当量 {rating_w}，容差 ±{FF_TOLERANCE}），整笔退"
                ),
            )
        final_ff = ff if ff is not None else ff_derived
        # 对表痕迹与进队同一次记账：同一连接同一事务，要么都落账要么都退
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, est_power_w, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, est_power_w, status,
                         verdict, reason, created_by, created_at, processed_at""",
            (code, voc, isc, final_ff, est, user["username"], now),
        ).fetchone()
        conn.execute(
            """INSERT INTO check_traces
               (scan_id, string_code, voc_v, isc_a, est_power_w, rating_w,
                ff_submitted, ff_derived, consistent, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s)""",
            (row["id"], code, voc, isc, est, rating_w, ff, ff_derived,
             user["username"], now),
        )
        conn.commit()
        return dump(row)


@get("/api/nameplates")
async def list_nameplates(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, rating_w, created_by, updated_by, created_at, updated_at
               FROM nameplate_ratings ORDER BY string_code"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/nameplates")
async def upsert_nameplate(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        rating = float(data.get("rating_w"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="铭牌当量必须是数字")
    if rating <= 0:
        raise HTTPException(status_code=400, detail="铭牌当量必须为正数")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO nameplate_ratings (string_code, rating_w, created_by, created_at)
               VALUES (%s,%s,%s,%s)
               ON CONFLICT (string_code) DO UPDATE
               SET rating_w = EXCLUDED.rating_w, updated_by = %s, updated_at = %s
               RETURNING id, string_code, rating_w, created_by, updated_by, created_at, updated_at""",
            (code, rating, user["username"], now, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/traces")
async def list_traces(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT t.id, t.scan_id, t.string_code, t.voc_v, t.isc_a, t.est_power_w,
                      t.rating_w, t.ff_submitted, t.ff_derived, t.consistent,
                      t.created_by, t.created_at,
                      s.status AS scan_status, s.verdict AS scan_verdict
               FROM check_traces t JOIN iv_scans s ON s.id = t.scan_id
               ORDER BY t.id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        list_nameplates,
        upsert_nameplate,
        list_traces,
    ]
)
