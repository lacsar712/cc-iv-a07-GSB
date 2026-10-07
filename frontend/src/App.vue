<template>
  <main>
    <h1>光伏组串IV扫描台</h1>
    <div v-if="!session">
      <p class="sub">扫描员提交组串开路电压、短路电流与填充因子；通知通道叫醒工人出结论。登录框已预填可写账号 scanner / scan123456。</p>
      <section>
        <label>用户名</label><input v-model="loginUser" autocomplete="off" />
        <label>密码</label><input type="password" v-model="loginPass" autocomplete="off" />
        <button :disabled="loading" @click="login">登录</button>
        <p v-if="error" class="err">{{ error }}</p>
      </section>
    </div>
    <div v-else>
      <nav class="topbar">
        <span class="who">已登录：{{ session.username }}（{{ isWriter ? "可提交" : "只读观察员" }}）</span>
        <button :class="{ active: view === 'records' }" @click="view = 'records'">扫描记录</button>
        <button :class="{ active: view === 'nameplate' }" @click="view = 'nameplate'">铭牌功率</button>
        <button class="secondary" @click="refresh">刷新</button>
        <button class="secondary" @click="logout">退出</button>
      </nav>
      <p v-if="error" class="err">{{ error }}</p>

      <div v-if="view === 'records'">
        <section>
          <table>
            <thead>
              <tr><th>编号</th><th>组串</th><th>Voc</th><th>Isc</th><th>估算功率</th><th>FF</th><th>状态</th><th>结论</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in logs" :key="row.id">
                <td>{{ row.id }}</td>
                <td>{{ row.string_code }}</td>
                <td>{{ row.voc_v ?? "—" }}</td>
                <td>{{ row.isc_a ?? "—" }}</td>
                <td>{{ row.est_power_w != null ? fmt(row.est_power_w, 1) : "—" }}</td>
                <td>{{ row.fill_factor }}</td>
                <td><span class="tag" :class="row.status === 'pending' ? 'pending' : 'ok'">{{ row.status === 'pending' ? '待处理' : '已完成' }}</span></td>
                <td><span v-if="row.verdict" class="tag" :class="row.verdict === '合格' ? 'ok' : 'bad'">{{ row.verdict }}</span><span v-else>—</span></td>
              </tr>
            </tbody>
          </table>
        </section>
      </div>

      <div v-else class="np-grid">
        <section>
          <h2>铭牌当量</h2>
          <template v-if="isWriter">
            <label>组串编号</label><input v-model="npCode" placeholder="例如 阵列C-串05" />
            <label>铭牌当量 W</label><input type="number" step="0.1" v-model="npRating" />
            <button :disabled="loading" @click="saveNameplate">登记 / 更新当量</button>
          </template>
          <p v-else class="hint">观察员只读，不能改当量。</p>
          <table>
            <thead><tr><th>组串</th><th>当量 W</th><th>登记人</th></tr></thead>
            <tbody>
              <tr v-for="np in nameplates" :key="np.id">
                <td>{{ np.string_code }}</td>
                <td>{{ fmt(np.rating_w, 1) }}</td>
                <td>{{ np.updated_by || np.created_by }}</td>
              </tr>
            </tbody>
          </table>
        </section>

        <section>
          <h2>提交出入口</h2>
          <template v-if="isWriter">
            <label>组串编号</label><input v-model="stringCode" placeholder="例如 阵列C-串05" />
            <label>开路电压 V（可留空）</label><input type="number" step="0.1" v-model="voc" />
            <label>短路电流 A（可留空）</label><input type="number" step="0.1" v-model="isc" />
            <label>填充因子（可留空）</label><input type="number" step="0.01" v-model="ff" />
            <button :disabled="loading" @click="submit">提交扫描</button>
          </template>
          <p v-else class="hint">观察员不能提交扫描。</p>
          <p class="hint">交开路电压＋短路电流：先折成估算功率，再对照铭牌当量推算填充因子；也可直填填充因子。两条路都交必须对得上（±{{ tol }}），对不上整笔退。对表痕迹与进队同一次记账。</p>
          <p v-if="submitMsg" class="ok-msg">{{ submitMsg }}</p>
          <p v-if="submitErr" class="err">{{ submitErr }}</p>
        </section>

        <section>
          <h2>对表痕迹</h2>
          <p v-if="!traces.length" class="hint">暂无痕迹。</p>
          <div v-for="t in traces" :key="t.id" class="trace">
            <div>
              <strong>#{{ t.scan_id }} {{ t.string_code }}</strong>
              <span class="tag" :class="t.consistent ? 'ok' : 'bad'">{{ t.consistent ? "对得上" : "对不上" }}</span>
              <span v-if="t.scan_verdict" class="tag" :class="t.scan_verdict === '合格' ? 'ok' : 'bad'">{{ t.scan_verdict }}</span>
              <span v-else class="tag pending">待处理</span>
            </div>
            <div class="trace-line">
              估算功率 {{ t.est_power_w != null ? fmt(t.est_power_w, 1) + " W" : "—" }}
              ／ 当量 {{ t.rating_w != null ? fmt(t.rating_w, 1) + " W" : "—" }}
            </div>
            <div class="trace-line">
              直填FF {{ t.ff_submitted != null ? t.ff_submitted : "—" }}
              ／ 推算FF {{ t.ff_derived != null ? fmt(t.ff_derived, 4) : "—" }}
            </div>
            <div class="trace-line dim">{{ t.created_by }} · {{ fmtTime(t.created_at) }}</div>
          </div>
        </section>
      </div>
    </div>
  </main>
</template>
<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
const FF_TOL = 0.05;
const session = ref(null);
const view = ref("records");
const logs = ref([]);
const nameplates = ref([]);
const traces = ref([]);
const loginUser = ref("scanner");
const loginPass = ref("scan123456");
const stringCode = ref("");
const voc = ref("");
const isc = ref("");
const ff = ref("");
const npCode = ref("");
const npRating = ref("");
const error = ref("");
const submitErr = ref("");
const submitMsg = ref("");
const loading = ref(false);
let timer;
const tol = FF_TOL;
const isWriter = computed(() => session.value?.role === "writer");
function headers() {
  return session.value ? { Authorization: "Bearer " + session.value.token } : {};
}
function fmt(val, digits) {
  return Number(val).toFixed(digits);
}
function fmtTime(iso) {
  return iso ? new Date(iso).toLocaleString() : "";
}
async function refresh() {
  if (!session.value) return;
  const opts = { headers: headers() };
  const [logsRes, npRes, trRes] = await Promise.all([
    fetch("/api/logs", opts),
    fetch("/api/nameplates", opts),
    fetch("/api/traces", opts),
  ]);
  if (logsRes.status === 401) { logout(); return; }
  if (logsRes.ok) logs.value = await logsRes.json();
  if (npRes.ok) nameplates.value = await npRes.json();
  if (trRes.ok) traces.value = await trRes.json();
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
    await refresh();
    timer = setInterval(refresh, 2000);
  } catch { error.value = "无法连接接口"; }
  finally { loading.value = false; }
}
function logout() {
  if (timer) clearInterval(timer);
  session.value = null;
  logs.value = [];
  nameplates.value = [];
  traces.value = [];
  localStorage.removeItem("pv_session");
}
async function submit() {
  submitErr.value = "";
  submitMsg.value = "";
  loading.value = true;
  try {
    const body = { string_code: stringCode.value };
    if (voc.value !== "") body.voc_v = Number(voc.value);
    if (isc.value !== "") body.isc_a = Number(isc.value);
    if (ff.value !== "") body.fill_factor = Number(ff.value);
    const res = await fetch("/api/logs", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok) { submitErr.value = data.detail || "提交失败"; return; }
    submitMsg.value = `已进队 #${data.id}，对表痕迹同次记账。`;
    stringCode.value = voc.value = isc.value = ff.value = "";
    await refresh();
  } catch { submitErr.value = "提交时网络异常"; }
  finally { loading.value = false; }
}
async function saveNameplate() {
  error.value = "";
  loading.value = true;
  try {
    const res = await fetch("/api/nameplates", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers() },
      body: JSON.stringify({ string_code: npCode.value, rating_w: Number(npRating.value) }),
    });
    const data = await res.json();
    if (!res.ok) { error.value = data.detail || "登记当量失败"; return; }
    npCode.value = npRating.value = "";
    await refresh();
  } catch { error.value = "登记当量时网络异常"; }
  finally { loading.value = false; }
}
onMounted(() => {
  const raw = localStorage.getItem("pv_session");
  if (raw) {
    try {
      session.value = JSON.parse(raw);
      refresh();
      timer = setInterval(refresh, 2000);
    } catch { localStorage.removeItem("pv_session"); }
  }
});
onUnmounted(() => { if (timer) clearInterval(timer); });
</script>
<style>
body { margin: 0; font-family: "Segoe UI", system-ui, sans-serif; background: #052e16; color: #ecfdf5; }
main { max-width: 1180px; margin: 0 auto; padding: 1.5rem; }
h1 { color: #86efac; margin: 0 0 0.25rem; }
h2 { color: #86efac; font-size: 1rem; margin: 0 0 0.75rem; }
.sub { color: #a7f3d0; margin-bottom: 1.25rem; }
section { background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 1rem 1.25rem; margin-bottom: 1rem; }
.topbar { display: flex; align-items: center; gap: 0.4rem; background: #14532d; border: 1px solid #166534; border-radius: 8px; padding: 0.6rem 1rem; margin-bottom: 1rem; }
.topbar .who { margin-right: auto; color: #a7f3d0; }
.topbar button.active { outline: 2px solid #86efac; }
.np-grid { display: grid; grid-template-columns: 1fr 1fr 1.2fr; gap: 1rem; align-items: start; }
.np-grid section { margin-bottom: 0; }
label { display: block; font-size: 0.85rem; margin-bottom: 0.25rem; }
input { width: 100%; box-sizing: border-box; padding: 0.5rem 0.65rem; border-radius: 6px; border: 1px solid #4ade80; background: #022c22; color: #ecfdf5; margin-bottom: 0.75rem; }
button { cursor: pointer; padding: 0.5rem 1rem; border: none; border-radius: 6px; background: #16a34a; color: #fff; font-weight: 600; margin-right: 0.4rem; }
button.secondary { background: #365314; }
.err { color: #fecaca; }
.ok-msg { color: #bbf7d0; }
.hint { color: #a7f3d0; font-size: 0.82rem; line-height: 1.5; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.45rem; border-bottom: 1px solid #166534; }
.tag { padding: 0.1rem 0.4rem; border-radius: 4px; font-size: 0.8rem; margin-left: 0.3rem; }
.ok { background: #14532d; color: #bbf7d0; }
.bad { background: #7f1d1d; color: #fecaca; }
.pending { background: #854d0e; color: #fde68a; }
.trace { border-bottom: 1px solid #166534; padding: 0.5rem 0; font-size: 0.88rem; }
.trace-line { color: #d1fae5; margin-top: 0.15rem; }
.trace-line.dim { color: #86efac; font-size: 0.78rem; }
</style>
