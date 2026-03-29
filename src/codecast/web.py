from __future__ import annotations

import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from .publisher import publish_with_opencli
from .storage import (
    STATUS_FAILED,
    STATUS_PENDING,
    STYLE_VALUES,
    connect,
    count_drafts,
    get_config,
    get_draft,
    init_db,
    list_drafts,
    list_publish_logs,
    list_recent_publish_activity,
    mark_publish_result,
    rerender_draft,
)


def _json(handler: BaseHTTPRequestHandler, code: int, payload: dict) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _text(handler: BaseHTTPRequestHandler, code: int, body: str) -> None:
    data = body.encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)


def _recommended_action(conn) -> str:
    pending = count_drafts(conn, STATUS_PENDING)
    failed = count_drafts(conn, STATUS_FAILED)
    if pending > 0:
        return "review"
    if failed > 0:
        return "retry_latest_failed"
    if not get_config(conn, "publish.opencli_cmd"):
        return "run_setup"
    return "wait_for_push"


def _latest_draft_id(conn, status: str | None = None) -> int | None:
    if status:
        row = conn.execute("SELECT id FROM drafts WHERE status = ? ORDER BY id DESC LIMIT 1", (status,)).fetchone()
    else:
        row = conn.execute("SELECT id FROM drafts ORDER BY id DESC LIMIT 1").fetchone()
    return int(row["id"]) if row else None


def _publish_one(conn, draft_id: int, dry_run: bool) -> tuple[int, str]:
    draft = get_draft(conn, draft_id)
    if not draft:
        return 1, f"Draft {draft_id} not found."
    configured_cmd = get_config(conn, "publish.opencli_cmd", "opencli twitter post") or "opencli twitter post"
    result = publish_with_opencli(draft["content"], base_command=configured_cmd, dry_run=dry_run)
    mark_publish_result(
        conn,
        draft_id=draft_id,
        command=result.command,
        return_code=result.return_code,
        stdout=result.stdout,
        stderr=result.stderr,
        dry_run=dry_run,
    )
    if result.return_code == 0:
        return 0, "Dry-run completed." if dry_run else "Published successfully."
    return 1, result.stderr or result.stdout or "Publish failed."


def _read_json(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


def _draft_to_dict(row) -> dict:
    return {
        "id": int(row["id"]),
        "status": row["status"],
        "style": row["style"],
        "title": row["title"],
        "content": row["content"],
        "repo_name": row["repo_name"],
        "repo_path": row["repo_path"],
        "created_at": row["created_at"],
    }


def _html_page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>CodeCast Web</title>
  <style>
    :root { --bg:#f4f6f8; --card:#ffffff; --text:#111827; --muted:#6b7280; --brand:#0f766e; --brand2:#0ea5e9; --danger:#b91c1c; }
    * { box-sizing:border-box; }
    body { margin:0; font-family:"SF Pro Text","PingFang SC","Segoe UI",sans-serif; color:var(--text); background:linear-gradient(135deg,#f8fafc,#eef2ff 60%,#ecfeff); }
    .wrap { max-width:1080px; margin:0 auto; padding:24px; }
    .hero { background:var(--card); border-radius:18px; padding:20px; box-shadow:0 8px 30px rgba(15,23,42,.08); animation:fadeIn .45s ease-out; }
    .title { font-size:24px; font-weight:700; margin:0 0 8px; }
    .sub { color:var(--muted); margin:0 0 16px; }
    .status { display:flex; gap:12px; flex-wrap:wrap; margin-bottom:14px; }
    .chip { background:#f8fafc; border:1px solid #e5e7eb; border-radius:999px; padding:6px 12px; font-size:13px; }
    .actions { display:flex; gap:10px; flex-wrap:wrap; }
    button { border:0; border-radius:12px; padding:10px 14px; font-weight:600; cursor:pointer; }
    .primary { background:linear-gradient(90deg,var(--brand),var(--brand2)); color:white; }
    .ghost { background:#f3f4f6; color:#111827; }
    .danger { background:#fee2e2; color:var(--danger); }
    .grid { margin-top:16px; display:grid; grid-template-columns:320px 1fr; gap:16px; }
    .card { background:var(--card); border-radius:16px; padding:14px; box-shadow:0 6px 20px rgba(15,23,42,.06); }
    .draft-list { max-height:460px; overflow:auto; display:flex; flex-direction:column; gap:8px; }
    .item { border:1px solid #e5e7eb; border-radius:10px; padding:10px; cursor:pointer; transition:.15s; }
    .item:hover { border-color:#99f6e4; transform:translateY(-1px); }
    .item.active { border-color:#0ea5e9; background:#f0f9ff; }
    .muted { color:var(--muted); font-size:12px; }
    textarea { width:100%; min-height:210px; border:1px solid #d1d5db; border-radius:10px; padding:12px; resize:vertical; font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace; }
    .row { display:flex; gap:8px; flex-wrap:wrap; margin:10px 0 0; }
    .bar { margin-top:14px; padding:10px 12px; border-radius:10px; font-size:13px; background:#f8fafc; border:1px solid #e5e7eb; }
    .bar.error { color:var(--danger); border-color:#fecaca; background:#fef2f2; }
    @media (max-width: 900px) { .grid { grid-template-columns:1fr; } }
    @keyframes fadeIn { from { opacity:0; transform:translateY(8px);} to { opacity:1; transform:translateY(0);} }
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1 class="title">CodeCast Web Client</h1>
      <p class="sub">先看状态，再做下一步。你只需要点击一个主动作。</p>
      <div class="status" id="statusRow"></div>
      <div class="actions">
        <button class="primary" id="doBtn">do</button>
        <button class="ghost" id="pendingBtn">查看待发布</button>
        <button class="ghost" id="refreshBtn">刷新</button>
      </div>
      <div id="message" class="bar">准备就绪。</div>
    </section>
    <section class="grid">
      <aside class="card">
        <h3>Drafts</h3>
        <div class="row">
          <button class="ghost" id="tabPending">Pending</button>
          <button class="ghost" id="tabAll">All</button>
        </div>
        <div class="draft-list" id="drafts"></div>
      </aside>
      <main class="card">
        <h3 id="editorTitle">选择草稿后可编辑与发布</h3>
        <div class="row">
          <button class="ghost" data-style="formal">formal</button>
          <button class="ghost" data-style="friendly">friendly</button>
          <button class="ghost" data-style="punchy">punchy</button>
        </div>
        <textarea id="content" readonly placeholder="这里会显示草稿正文"></textarea>
        <div class="row">
          <button class="ghost" id="dryRunBtn">Dry Run</button>
          <button class="primary" id="publishBtn">Publish</button>
          <button class="danger" id="historyBtn">History</button>
        </div>
        <div class="bar" id="historyBox">最近日志会显示在这里。</div>
      </main>
    </section>
  </div>
  <script>
    let currentStatus = "pending";
    let selectedDraft = null;
    let latestAction = "wait_for_push";

    async function req(url, opts) {
      const res = await fetch(url, opts || {});
      return res.json();
    }

    function toast(text, isError) {
      const bar = document.getElementById("message");
      bar.textContent = text;
      bar.className = isError ? "bar error" : "bar";
    }

    async function loadStatus() {
      const data = await req("/api/status");
      latestAction = data.next_action;
      const row = document.getElementById("statusRow");
      row.innerHTML = "";
      ["pending","failed","selected"].forEach((k) => {
        const el = document.createElement("div");
        el.className = "chip";
        el.textContent = k + ": " + data[k];
        row.appendChild(el);
      });
      document.getElementById("doBtn").textContent = "do (" + latestAction + ")";
    }

    async function loadDrafts() {
      const data = await req("/api/drafts?scope=" + currentStatus);
      const list = document.getElementById("drafts");
      list.innerHTML = "";
      if (!data.items.length) {
        const empty = document.createElement("div");
        empty.className = "muted";
        empty.textContent = "暂无草稿";
        list.appendChild(empty);
        return;
      }
      data.items.forEach((d) => {
        const item = document.createElement("div");
        item.className = "item" + ((selectedDraft && selectedDraft.id === d.id) ? " active" : "");
        item.innerHTML = "<div><b>#"+d.id+"</b> "+d.status+"</div><div class='muted'>"+(d.repo_name || "multi-repo")+"</div>";
        item.onclick = () => loadDraft(d.id);
        list.appendChild(item);
      });
    }

    async function loadDraft(id) {
      const data = await req("/api/drafts/" + id);
      if (!data.ok) return toast(data.message, true);
      selectedDraft = data.item;
      document.getElementById("editorTitle").textContent = "#" + selectedDraft.id + " · " + selectedDraft.title;
      document.getElementById("content").value = selectedDraft.content;
      await loadDrafts();
      await loadStatus();
    }

    async function doAction() {
      const data = await req("/api/do", { method:"POST" });
      toast(data.message, !data.ok);
      if (data.draft_id) await loadDraft(data.draft_id);
      await loadStatus();
      await loadDrafts();
    }

    async function applyStyle(style) {
      if (!selectedDraft) return toast("请先选择草稿", true);
      const data = await req("/api/drafts/" + selectedDraft.id + "/style", {
        method:"POST",
        headers:{ "Content-Type":"application/json" },
        body: JSON.stringify({ style })
      });
      toast(data.message, !data.ok);
      if (data.ok) await loadDraft(selectedDraft.id);
    }

    async function publish(dryRun) {
      if (!selectedDraft) return toast("请先选择草稿", true);
      const path = dryRun ? "/dry-run" : "/publish";
      const data = await req("/api/drafts/" + selectedDraft.id + path, { method:"POST" });
      toast(data.message, !data.ok);
      await loadStatus();
      await loadDrafts();
    }

    async function loadHistory() {
      if (!selectedDraft) return toast("请先选择草稿", true);
      const data = await req("/api/drafts/" + selectedDraft.id + "/history");
      const box = document.getElementById("historyBox");
      if (!data.items.length) {
        box.textContent = "暂无发布日志";
        return;
      }
      box.textContent = data.items.map((x) => "#" + x.id + " rc=" + x.return_code + " " + x.attempted_at).join(" | ");
    }

    document.getElementById("doBtn").onclick = doAction;
    document.getElementById("pendingBtn").onclick = async () => { currentStatus = "pending"; await loadDrafts(); };
    document.getElementById("refreshBtn").onclick = async () => { await loadStatus(); await loadDrafts(); toast("已刷新", false); };
    document.getElementById("tabPending").onclick = async () => { currentStatus = "pending"; await loadDrafts(); };
    document.getElementById("tabAll").onclick = async () => { currentStatus = "all"; await loadDrafts(); };
    document.getElementById("dryRunBtn").onclick = async () => publish(true);
    document.getElementById("publishBtn").onclick = async () => publish(false);
    document.getElementById("historyBtn").onclick = loadHistory;
    document.querySelectorAll("[data-style]").forEach((b) => b.onclick = async () => applyStyle(b.dataset.style));
    (async () => { await loadStatus(); await loadDrafts(); })();
  </script>
</body>
</html>
"""


def make_handler(db_path: str | None):
    class Handler(BaseHTTPRequestHandler):
        def _conn(self):
            conn = connect(db_path)
            init_db(conn)
            return conn

        def _not_found(self):
            _json(self, HTTPStatus.NOT_FOUND, {"ok": False, "message": "not found"})

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/":
                _text(self, HTTPStatus.OK, _html_page())
                return
            conn = self._conn()
            try:
                if parsed.path == "/api/status":
                    selected = _latest_draft_id(conn)
                    _json(
                        self,
                        HTTPStatus.OK,
                        {
                            "ok": True,
                            "pending": count_drafts(conn, STATUS_PENDING),
                            "failed": count_drafts(conn, STATUS_FAILED),
                            "selected": selected,
                            "next_action": _recommended_action(conn),
                        },
                    )
                    return
                if parsed.path == "/api/drafts":
                    q = parse_qs(parsed.query or "")
                    scope = q.get("scope", ["pending"])[0]
                    status = None if scope == "all" else STATUS_PENDING
                    rows = [_draft_to_dict(x) for x in list_drafts(conn, status=status)]
                    _json(self, HTTPStatus.OK, {"ok": True, "items": rows})
                    return
                if parsed.path.startswith("/api/drafts/") and parsed.path.endswith("/history"):
                    draft_id_text = parsed.path.split("/")[3]
                    draft_id = int(draft_id_text)
                    rows = list_publish_logs(conn, draft_id, limit=20)
                    items = [
                        {
                            "id": int(r["id"]),
                            "attempted_at": r["attempted_at"],
                            "return_code": int(r["return_code"]),
                            "dry_run": int(r["dry_run"]),
                        }
                        for r in rows
                    ]
                    _json(self, HTTPStatus.OK, {"ok": True, "items": items})
                    return
                if parsed.path.startswith("/api/drafts/"):
                    draft_id_text = parsed.path.split("/")[3]
                    draft_id = int(draft_id_text)
                    draft = get_draft(conn, draft_id)
                    if not draft:
                        _json(self, HTTPStatus.NOT_FOUND, {"ok": False, "message": "draft not found"})
                        return
                    _json(self, HTTPStatus.OK, {"ok": True, "item": _draft_to_dict(draft)})
                    return
                if parsed.path == "/api/history":
                    rows = list_recent_publish_activity(conn, limit=20)
                    items = [
                        {
                            "id": int(r["id"]),
                            "attempted_at": r["attempted_at"],
                            "return_code": int(r["return_code"]),
                            "draft_id": int(r["draft_id"]),
                            "repo_name": r["repo_name"],
                        }
                        for r in rows
                    ]
                    _json(self, HTTPStatus.OK, {"ok": True, "items": items})
                    return
                self._not_found()
            except Exception as exc:
                _json(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
            finally:
                conn.close()

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            conn = self._conn()
            try:
                if parsed.path == "/api/do":
                    action = _recommended_action(conn)
                    if action == "review":
                        draft_id = _latest_draft_id(conn, STATUS_PENDING)
                        if draft_id is None:
                            _json(self, HTTPStatus.OK, {"ok": False, "message": "No pending drafts."})
                            return
                        _json(self, HTTPStatus.OK, {"ok": True, "message": f"Opened draft #{draft_id}.", "draft_id": draft_id})
                        return
                    if action == "retry_latest_failed":
                        draft_id = _latest_draft_id(conn, STATUS_FAILED)
                        if draft_id is None:
                            _json(self, HTTPStatus.OK, {"ok": False, "message": "No failed drafts."})
                            return
                        rc, message = _publish_one(conn, draft_id, dry_run=False)
                        _json(self, HTTPStatus.OK, {"ok": rc == 0, "message": message, "draft_id": draft_id})
                        return
                    if action == "run_setup":
                        _json(self, HTTPStatus.OK, {"ok": True, "message": "请先配置 publish.opencli_cmd，再推送代码。"})
                        return
                    _json(self, HTTPStatus.OK, {"ok": True, "message": "等待下一次 push 自动生成草稿。"})
                    return
                if parsed.path.startswith("/api/drafts/") and parsed.path.endswith("/style"):
                    draft_id = int(parsed.path.split("/")[3])
                    payload = _read_json(self)
                    style = str(payload.get("style", "")).strip()
                    if style not in STYLE_VALUES:
                        _json(self, HTTPStatus.BAD_REQUEST, {"ok": False, "message": "Invalid style"})
                        return
                    rerender_draft(conn, draft_id, style)
                    _json(self, HTTPStatus.OK, {"ok": True, "message": f"Draft #{draft_id} rerendered to {style}."})
                    return
                if parsed.path.startswith("/api/drafts/") and parsed.path.endswith("/dry-run"):
                    draft_id = int(parsed.path.split("/")[3])
                    rc, message = _publish_one(conn, draft_id, dry_run=True)
                    _json(self, HTTPStatus.OK, {"ok": rc == 0, "message": message})
                    return
                if parsed.path.startswith("/api/drafts/") and parsed.path.endswith("/publish"):
                    draft_id = int(parsed.path.split("/")[3])
                    rc, message = _publish_one(conn, draft_id, dry_run=False)
                    _json(self, HTTPStatus.OK, {"ok": rc == 0, "message": message})
                    return
                self._not_found()
            except Exception as exc:
                _json(self, HTTPStatus.INTERNAL_SERVER_ERROR, {"ok": False, "message": str(exc)})
            finally:
                conn.close()

        def log_message(self, format, *args):  # noqa: A003
            return

    return Handler


def run_web_server(db_path: str | None, host: str, port: int) -> None:
    server = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"CodeCast Web running at http://{host}:{port}")  # noqa: T201
    server.serve_forever()

