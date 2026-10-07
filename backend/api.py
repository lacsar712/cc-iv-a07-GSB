import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import LOOKUP_SEED, SCHEMA, connect
from nameplate import LookupError, estimate_power, resolve, to_equivalent
from rules import judge

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
        # 对照表种子
        for equiv, std_ff, note in LOOKUP_SEED:
            conn.execute(
                """INSERT INTO ff_lookup (power_equiv_w, std_fill_factor, note, updated_by, updated_at)
                   VALUES (%s,%s,%s,'scanner',%s)
                   ON CONFLICT (power_equiv_w) DO NOTHING""",
                (equiv, std_ff, note, now),
            )
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            # (编号, voc, isc, ff, 期望结论)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                est = estimate_power(voc, isc)
                equiv = to_equivalent(est)
                row = conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, est_power_w, power_equiv_w,
                        status, verdict, reason, created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)
                       RETURNING id""",
                    (code, voc, isc, ff, est, equiv, verdict, reason, now, now),
                ).fetchone()
                conn.execute(
                    """INSERT INTO lookup_traces
                       (scan_id, string_code, voc_v, isc_a, est_power_w, power_equiv_w,
                        ff_from_table, ff_direct, ff_used, input_mode, match, verdict,
                        created_by, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'vi+ff','exact',%s,'scanner',%s)""",
                    (row["id"], code, voc, isc, est, equiv, ff, ff, ff, verdict, now),
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
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail="仅扫描员可提交，观察员只读")
    return user


def as_float(value):
    if value is None or value == "":
        return None
    return float(value)


def as_int(value):
    if value is None or value == "":
        return None
    return int(float(value))


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
            """SELECT id, string_code, voc_v, isc_a, fill_factor, est_power_w, power_equiv_w,
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
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    est = estimate_power(voc, isc)
    with connect() as conn:
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, est_power_w, power_equiv_w,
                status, created_by, created_at)
               VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, est_power_w, power_equiv_w,
                         status, verdict, reason, created_by, created_at, processed_at""",
            (code, voc, isc, ff, est, to_equivalent(est), user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


@get("/api/nameplate/lookup")
async def nameplate_lookup(request: Request) -> list:
    # 观察员也能看对照表
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT power_equiv_w, std_fill_factor, note, updated_by, updated_at
               FROM ff_lookup ORDER BY power_equiv_w"""
        ).fetchall()
        return [dump(r) for r in rows]


@get("/api/nameplate/traces")
async def nameplate_traces(request: Request) -> list:
    # 观察员能看对表痕迹，但改不了当量
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, scan_id, string_code, voc_v, isc_a, est_power_w, power_equiv_w,
                      ff_from_table, ff_direct, ff_used, input_mode, match, verdict,
                      created_by, created_at
               FROM lookup_traces ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/nameplate/submit", status_code=201)
async def nameplate_submit(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = as_float(data.get("voc_v"))
        isc = as_float(data.get("isc_a"))
        equiv_in = as_int(data.get("power_equiv_w"))
        ff_direct = as_float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压、电流、当量、填充因子必须是数字")

    if voc is not None and voc <= 0:
        raise HTTPException(status_code=400, detail="开路电压必须为正")
    if isc is not None and isc <= 0:
        raise HTTPException(status_code=400, detail="短路电流必须为正")
    if equiv_in is not None and equiv_in <= 0:
        raise HTTPException(status_code=400, detail="铭牌当量必须为正")
    if ff_direct is not None and not (0 < ff_direct <= 1):
        raise HTTPException(status_code=400, detail="填充因子应在 0 到 1 之间")

    now = datetime.now(timezone.utc)
    with connect() as conn:
        # 先折算当量再对表；当量必须在对照表里
        if voc is not None and isc is not None:
            probe_equiv = to_equivalent(estimate_power(voc, isc))
        else:
            probe_equiv = equiv_in
        if probe_equiv is None:
            raise HTTPException(status_code=400, detail="要么交电压电流折算，要么左填铭牌当量")
        table = conn.execute(
            """SELECT power_equiv_w, std_fill_factor FROM ff_lookup
               WHERE power_equiv_w = %s""",
            (probe_equiv,),
        ).fetchone()
        if table is None:
            raise HTTPException(
                status_code=400,
                detail=f"当量 {probe_equiv}W 在对照表中无档，整笔退回",
            )
        try:
            r = resolve(
                voc, isc, equiv_in, ff_direct,
                float(table["std_fill_factor"]), int(table["power_equiv_w"]),
            )
        except LookupError as exc:
            # 对不上：整笔退，什么都不写
            raise HTTPException(status_code=400, detail=str(exc))

        verdict, reason = judge(r.ff_used)

        # 对表痕迹与进队必须是同一次记账（同一事务）：
        # 插 iv_scans 触 NOTIFY 叫醒工人，紧接着写痕迹，一起提交或一起回滚。
        with conn.transaction():
            scan = conn.execute(
                """INSERT INTO iv_scans
                   (string_code, voc_v, isc_a, fill_factor, est_power_w, power_equiv_w,
                    status, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,'pending',%s,%s)
                   RETURNING id, string_code, voc_v, isc_a, fill_factor, est_power_w,
                             power_equiv_w, status, verdict, reason,
                             created_by, created_at, processed_at""",
                (code, voc, isc, r.ff_used,
                 r.est_power_w, r.power_equiv_w, user["username"], now),
            ).fetchone()
            trace = conn.execute(
                """INSERT INTO lookup_traces
                   (scan_id, string_code, voc_v, isc_a, est_power_w, power_equiv_w,
                    ff_from_table, ff_direct, ff_used, input_mode, match, verdict,
                    created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   RETURNING id, scan_id, string_code, voc_v, isc_a, est_power_w,
                             power_equiv_w, ff_from_table, ff_direct, ff_used,
                             input_mode, match, verdict, created_by, created_at""",
                (scan["id"], code, voc, isc, r.est_power_w, r.power_equiv_w,
                 r.ff_from_table, r.ff_direct, r.ff_used, r.input_mode, r.match,
                 verdict, user["username"], now),
            ).fetchone()
        conn.commit()
        return {"scan": dump(scan), "trace": dump(trace)}


app = Litestar(route_handlers=[
    health, login, list_logs, create_log,
    nameplate_lookup, nameplate_traces, nameplate_submit,
])
