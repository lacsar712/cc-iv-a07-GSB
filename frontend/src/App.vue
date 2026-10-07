<template>
  <main>
    <nav class="topbar">
      <h1>光伏组串IV扫描台</h1>
      <div class="tabs">
        <button :class="{ active: page === 'scan' }" @click="page = 'scan'">IV扫描</button>
        <button :class="{ active: page === 'nameplate' }" @click="page = 'nameplate'">铭牌功率专页</button>
      </div>
    </nav>

    <div v-if="!session">
      <p class="sub">扫描员提交开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456，观察员 watcher / watch123456 只读。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>

    <div v-else>
      <section class="barline">
        <span>已登录：{{ session.username }}（{{ isWriter ? "可提交" : "观察员只读" }}）</span>
        <span><button class="secondary" @click="logout">退出</button>
        <button class="secondary" @click="refreshAll">刷新</button></span>
      </section>

      <!-- ============ IV 扫描主页 ============ -->
      <template v-if="page === 'scan'">
        <section v-if="isWriter">
          <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
          <label>开路电压 V</label><input type="number" step="0.1" v-model="voc" />
          <label>短路电流 A</label><input type="number" step="0.1" v-model="isc" />
          <label>填充因子</label><input type="number" step="0.01" v-model="ff" />
          <button :disabled="loading" @click="submit">提交扫描</button>
          <p v-if="error" class="err">{{ error }}</p>
        </section>
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>估算功率W</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v ?? "—" }}</td>
                <td>{{ row.isc_a ?? "—" }}</td>
                <td>{{ row.est_power_w ?? "—" }}</td>
                <td>{{ row.fill_factor ?? "—" }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </template>

      <!-- ============ 铭牌功率专页 ============ -->
      <template v-else>
        <p class="sub">开路电压 × 短路电流先折成估算功率、取整成当量再跟对照表取标准填充因子；也可直填填充因子走第二条路，两条路必须对得上，对不上整笔退回。对表痕迹与扫描进队同一次记账。</p>
        <div class="cols">
          <!-- 左：填当量 -->
          <section>
            <h2>① 左填当量</h2>
            <fieldset :disabled="!isWriter" class="ro">
              <label>组串编号</label><input v-model="np.code" placeholder="例如 阵列C-串05" />
              <label>开路电压 Voc（V）</label><input type="number" step="0.1" v-model="np.voc" placeholder="交电压电流自动折算" />
              <label>短路电流 Isc（A）</label><input type="number" step="0.1" v-model="np.isc" placeholder="交电压电流自动折算" />
              <div class="fold">估算功率：<b>{{ preview.estPower ?? "—" }}</b> W ｜ 折算当量：<b>{{ preview.equiv ?? "—" }}</b> W</div>
              <label>或直接手填铭牌当量（W）</label><input type="number" step="1" v-model="np.equiv" placeholder="可不填；填了要与折算一致" />
              <label>直填填充因子（第二条路，可选）</label><input type="number" step="0.01" min="0" max="1" v-model="np.ff" placeholder="直填须与对照表对得上" />
            </fieldset>
            <p v-if="!isWriter" class="hint">观察员只读：不能填当量、不能提交，可看右栏对表痕迹。</p>
          </section>

          <!-- 中：出入口 / 对照表 + 核对 -->
          <section>
            <h2>② 中出入口 · 对表</h2>
            <table class="lookup">
              <thead><tr><th>当量 W</th><th>标准 FF</th><th>结论</th><th>备注</th></tr></thead>
              <tbody>
                <tr v-for="r in lookup" :key="r.power_equiv_w" :class="{ hit: r.power_equiv_w === preview.equiv }">
                  <td>{{ r.power_equiv_w }}</td>
                  <td>{{ r.std_fill_factor }}</td>
                  <td><span class="tag" :class="r.std_fill_factor >= 0.72 ? 'ok' : 'bad'">{{ r.std_fill_factor >= 0.72 ? '合格' : '衰减' }}</span></td>
                  <td class="note">{{ r.note || '' }}</td>
                </tr>
              </tbody>
            </table>
            <div class="check">
              <p>折算当量：<b>{{ preview.equiv ?? "—" }}</b> W
                <span v-if="preview.equiv && !preview.row" class="tag bad">对照表无此档 → 整笔退</span>
                <span v-else-if="preview.equiv && preview.row" class="tag ok">已命中对照表</span>
              </p>
              <p v-if="preview.equivClash" class="tag bad">手填当量 {{ np.equiv }} 与折算当量 {{ preview.equiv }} 对不上 → 整笔退</p>
              <p>表内标准 FF：<b>{{ preview.row ? preview.row.std_fill_factor : "—" }}</b></p>
              <p v-if="preview.ff !== null">直填 FF：<b>{{ preview.ff }}</b>
                <span v-if="preview.row" class="tag" :class="preview.ffMatch ? 'ok' : 'bad'">
                  {{ preview.ffMatch ? "两路对得上" : "与对照表对不上 → 整笔退" }}
                </span>
              </p>
              <p>将采用 FF：<b>{{ preview.ffUsed ?? "—" }}</b>
                <span v-if="preview.ffUsed !== null" class="tag" :class="preview.verdict === '合格' ? 'ok' : 'bad'">{{ preview.verdict }}</span>
              </p>
              <button v-if="isWriter" :disabled="loading || !preview.canSubmit" @click="submitNameplate">对表并进队</button>
              <p v-if="npError" class="err">{{ npError }}</p>
              <p v-if="npOk" class="ok-text">{{ npOk }}</p>
            </div>
          </section>

          <!-- 右：对表痕迹 -->
          <section>
            <h2>③ 右列对表痕迹</h2>
            <table class="traces">
              <thead><tr><th>#</th><th>组串</th><th>当量</th><th>表FF</th><th>直填FF</th><th>采用</th><th>核对</th><th>结论</th></tr></thead>
              <tbody>
                <tr v-for="t in traces" :key="t.id">
                  <td>{{ t.scan_id ?? "—" }}</td>
                  <td>{{ t.string_code }}</td>
                  <td>{{ t.power_equiv_w }}</td>
                  <td>{{ t.ff_from_table }}</td>
                  <td>{{ t.ff_direct ?? "—" }}</td>
                  <td>{{ t.ff_used }}</td>
                  <td><span class="tag" :class="t.match === 'mismatch' ? 'bad' : 'ok'">{{ matchLabel(t.match) }}</span></td>
                  <td><span class="tag" :class="t.verdict === '合格' ? 'ok' : 'bad'">{{ t.verdict }}</span></td>
                </tr>
                <tr v-if="!traces.length"><td colspan="8" class="hint">暂无对表痕迹</td></tr>
              </tbody>
            </table>
          </section>
        </div>
      </template>
    </div>
  </main>
</template>

<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from "vue";
const session = ref(null);
const page = ref("scan");
const logs = ref([]);
const lookup = ref([]);
const traces = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const np = reactive({ code: "", voc: "", isc: "", equiv: "", ff: "" });
const error = ref("");
const npError = ref("");
const npOk = ref("");
const loading = ref(false);
let timer;
const isWriter = computed(() => session.value?.role === "writer");

function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function num(v) {
  if (v === null || v === undefined || String(v).trim() === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : NaN;
}
function matchLabel(m) {
  return { exact: "两路一致", table_only: "对表取值", mismatch: "对不上" }[m] || m;
}

const preview = computed(() => {
  const out = { estPower: null, equiv: null, row: null, ff: null, ffMatch: false,
                ffUsed: null, verdict: null, canSubmit: false, equivClash: false };
  const v = num(np.voc), i = num(np.isc), eq = num(np.equiv), f = num(np.ff);
  if (Number.isNaN(v) || Number.isNaN(i) || Number.isNaN(eq) || Number.isNaN(f)) return out;
  const hasVI = v !== null && i !== null;
  const hasEq = eq !== null;
  if (!hasVI && !hasEq) return out;
  let equiv;
  if (hasVI) {
    if (v <= 0 || i <= 0) return out;
    out.estPower = Math.round(v * i * 100) / 100;
    equiv = Math.round(out.estPower);
    out.equiv = equiv;
    if (hasEq && eq !== equiv) out.equivClash = true;
  } else {
    if (eq <= 0) return out;
    equiv = eq;
    out.equiv = equiv;
  }
  const row = lookup.value.find(r => r.power_equiv_w === equiv) || null;
  out.row = row;
  if (!row || out.equivClash) return out;
  out.ff = f;
  if (f !== null) {
    if (!(f > 0 && f <= 1)) return out;
    out.ffMatch = Math.round(Math.abs(f - row.std_fill_factor) * 1e6) / 1e6 <= 0.02;
    if (!out.ffMatch) return out;
    out.ffUsed = f;
  } else {
    out.ffUsed = row.std_fill_factor;
  }
  out.verdict = out.ffUsed >= 0.72 ? "合格" : "衰减";
  out.canSubmit = !!np.code.trim();
  return out;
});

async function refreshAll() {
  if (!session.value) return;
  const [r1, r2, r3] = await Promise.all([
    fetch("/api/logs", { headers: headers() }),
    fetch("/api/nameplate/lookup", { headers: headers() }),
    fetch("/api/nameplate/traces", { headers: headers() }),
  ]);
  if (r1.status === 401) { logout(); return; }
  if (r1.ok) logs.value = await r1.json();
  if (r2.ok) lookup.value = await r2.json();
  if (r3.ok) traces.value = await r3.json();
}
async function login() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: loginUser.value, password: loginPass.value }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登录失败"; return; }
    session.value = { token: data.access_token, username: data.username, role: data.role };
    localStorage.setItem("pv_session", JSON.stringify(session.value));
    await refreshAll();
    timer = setInterval(refreshAll, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({
        string_code: stringCode.value,
        voc_v: Number(voc.value),
        isc_a: Number(isc.value),
        fill_factor: Number(ff.value),
      }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "提交失败"; return; }
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refreshAll();
  } catch { error.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function submitNameplate() {
  npError.value = "";
  npOk.value = "";
  loading.value = true;
  try {
    const body = { string_code: np.code.trim() };
    if (np.voc !== "") body.voc_v = Number(np.voc);
    if (np.isc !== "") body.isc_a = Number(np.isc);
    if (np.equiv !== "") body.power_equiv_w = Number(np.equiv);
    if (np.ff !== "") body.fill_factor = Number(np.ff);
    const res = await fetch("/api/nameplate/submit", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) {
      npError.value = data.detail || "提交失败";
      await refreshAll();
      return;
    }
    npOk.value = `已进队 #${data.scan.id}，当量 ${data.trace.power_equiv_w}W，采用 FF ${data.trace.ff_used}，对表痕迹同笔记账`;
    np.code = np.voc = np.isc = np.equiv = np.ff = "";
    await refreshAll();
  } catch { npError.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refreshAll();
      timer = setInterval(refreshAll, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>

<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1280px; margin: 0 auto; padding: 1.5rem; }
.topbar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 0.5rem; }
h1 { color: #86efac; margin: 0; font-size: 1.4rem; }
.tabs button { background: #14532d; border: 1px solid #166534; }
.tabs button.active { background: #16a34a; }
.sub { color: #a7f3d0; margin: 1rem 0; }
.barline { display: flex; justify-content: space-between; align-items: center; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.cols { display: grid; grid-template-columns: 1fr 1.1fr 1.3fr; gap: 1rem; align-items: start; }
@media (max-width: 1000px) { .cols { grid-template-columns: 1fr; } }
h2 { font-size: 1rem; color: #86efac; margin: 0 0 0.75rem; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.6rem; }
fieldset.ro { border: none; padding: 0; margin: 0; }
fieldset.ro:disabled { opacity: 0.75; }
.fold { font-size: 0.85rem; background: #022c22; border-radius: 6px; padding: 0.5rem 0.65rem; margin-bottom: 0.6rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button:disabled { opacity: 0.5; cursor: not-allowed; }
button.secondary { background: #365314; }
.err { color: #fecaca; }
.ok-text { color: #bbf7d0; }
.hint { color: #fde68a; font-size: 0.85rem; }
.note { color: #a7f3d0; font-size: 0.8rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { text-align: left; padding: 0.4rem; border-bottom: 1px solid #166534; vertical-align: top; }
.lookup tr.hit { background: #166534; }
.check p { margin: 0.4rem 0; font-size: 0.9rem; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.78rem; white-space: nowrap; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.traces { font-size: 0.78rem; }
.traces th, .traces td { padding: 0.3rem; }
</style>
