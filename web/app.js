/* ==========================================================================
   分身 · 数字人口播 — H5 controller
   ========================================================================== */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const IC = (name, cls = "ic") => `<svg class="${cls}"><use href="#i-${name}"/></svg>`;
  const CHARS_PER_SEC = 4.4;
  const MAX_CHARS = 1200;
  const COMFORT_CHARS = 900;
  const WORKS_PAGE_SIZE = 10;

  const state = {
    screen: "home",
    avatars: [],
    tasks: [],
    avatar: null,
    task: null,
    cues: [],
    pollTimer: null,
    setupTimer: null,
    avatarTimer: null,
    elapsedTimer: null,
    startedAt: 0,
    previewText: "",
    previewPlaying: false,
    uploading: false,
    aiBusy: false,
    generating: false,
    loaded: false,
    ringValue: 0,
    draftTimer: null,
    lastAnnounced: "",
    taskStartedAt: 0,
    pollErrors: 0,
    worksPage: 1,
    worksPages: 1,
    taskStats: { success: 0, failed: 0, running: 0 },
  };

  /* ------------------------------------------------------------------ utils */
  const esc = (value = "") =>
    String(value).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  const fmtTime = (seconds) => {
    if (!Number.isFinite(seconds) || seconds < 0) seconds = 0;
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${String(s).padStart(2, "0")}`;
  };

  const fmtClock = (seconds) => {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  };

  const fmtAgo = (ts) => {
    if (!ts) return "";
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return "刚刚";
    if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
    if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
    return `${Math.floor(diff / 86400)} 天前`;
  };

  const countChars = (text) => text.replace(/\s/g, "").length;
  const estimate = (text) => Math.max(0, Math.round(countChars(text) / CHARS_PER_SEC));

  let toastTimer = null;
  function toast(message, kind = "", actionLabel = "", onAction = null, duration = 2600) {
    const el = $("#toast");
    $("#toast-text").textContent = message;
    el.className = `toast show ${kind}`;
    const action = $("#toast-action");
    if (actionLabel && onAction) {
      action.hidden = false;
      action.textContent = actionLabel;
      action.onclick = () => { closeToast(); onAction(); };
    } else {
      action.hidden = true;
      action.onclick = null;
    }
    clearTimeout(toastTimer);
    toastTimer = setTimeout(closeToast, duration);
  }
  function closeToast() {
    const el = $("#toast");
    el.className = "toast";
    clearTimeout(toastTimer);
  }
  function announce(message) {
    const sr = $("#sr-status");
    if (sr) { sr.textContent = ""; requestAnimationFrame(() => (sr.textContent = message)); }
  }

  async function api(path, options = {}) {
    const { method = "GET", body, form } = options;
    const init = { method, headers: {} };
    if (form) {
      init.body = form;
    } else if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const response = await fetch(path, init);
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok) {
      const detail = data && data.detail;
      const message =
        typeof detail === "string"
          ? detail
          : detail && detail.message
            ? detail.message
            : `请求失败（${response.status}）`;
      throw new Error(message);
    }
    return data;
  }

  /* --------------------------------------------------------------- routing */
  const SCREENS = ["home", "create", "works", "progress", "result"];
  const NAV_ORDER = { home: 0, create: 1, works: 2, progress: 3, result: 4 };
  const scrollMemory = Object.create(null);
  function forwardFor(from, to) {
    // progress/result reuse the create/home context; treat both as forward from them
    const a = NAV_ORDER[from] ?? 0;
    const b = NAV_ORDER[to] ?? 0;
    if (from === to) return 1;
    return b >= a ? 1 : -1;
  }

  function showScreen(name, options = {}) {
    if (!SCREENS.includes(name)) name = "home";
    const dir = forwardFor(state.screen, name);
    const screens = $$(".screen");
    if (name !== state.screen) {
      const leaving = screens.find((el) => el.dataset.screen === state.screen);
      if (leaving) scrollMemory[state.screen] = leaving.scrollTop;
    }
    screens.forEach((el) => {
      const isTarget = el.dataset.screen === name;
      el.style.setProperty("--nav-x", isTarget ? `${dir * 18}px` : `${dir * -18}px`);
      el.classList.toggle("is-active", isTarget);
      el.toggleAttribute("inert", !isTarget);
      if (isTarget) el.scrollTop = scrollMemory[name] || 0;
    });
    state.screen = name;
    const historyMode = options.history || (name === "progress" || name === "result" ? "replace" : "push");
    if (historyMode === "push" && name !== (history.state && history.state.screen)) {
      history.pushState({ screen: name }, "");
    } else if (historyMode === "replace" || historyMode === "none") {
      history.replaceState({ screen: name }, "");
    }
    $$(".tab").forEach((el) =>
      el.classList.toggle("is-active", el.dataset.tab === (name === "progress" || name === "result" ? "" : name))
    );
    document.body.classList.toggle("immersive", name === "progress" || name === "result");
    if (name === "create") renderCompose();
    if (name === "home") renderHome();
    if (name === "works") loadWorksPage(1);
    syncTwinVideos();
  }

  function goHome() {
    stopTaskPolling();
    showScreen("home");
    refreshAll();
  }

  /* ------------------------------------------------------------------ data */
  async function refreshAll() {
    try {
      const [avatars, tasks] = await Promise.all([
        api("/api/avatars"),
        api("/api/tasks?limit=24"),
      ]);
      state.avatars = avatars.items || [];
      state.tasks = tasks.items || [];
      state.taskStats = tasks.counts || { success: 0, failed: 0, running: 0 };
      state.loaded = true;
      const savedId = localStorage.getItem("dh.avatarId");
      state.avatar =
        state.avatars.find((a) => a.id === savedId && a.status === "ready") ||
        state.avatars.find((a) => a.status === "ready") ||
        state.avatars[0] ||
        null;
      if (state.avatar) localStorage.setItem("dh.avatarId", state.avatar.id);
    } catch (error) {
      toast(error.message, "err");
    }
    renderHome();
  }

  function markHScroll(el) {
    if (!el) return;
    const update = () => el.classList.toggle("is-scrollable", el.scrollWidth - el.clientWidth > 4);
    update();
    el.addEventListener("scroll", update, { passive: true });
    window.addEventListener("resize", update);
  }

  /* ------------------------------------------------------------------ home */
  function renderHomeSkeleton() {
    const zone = $("#avatar-zone");
    if (!zone) return;
    zone.innerHTML = `
      <div class="zone-head is-skeleton" aria-hidden="true">
        <span class="sk sk-line" style="width:86px;height:14px;margin:0"></span>
        <span class="sk sk-line" style="width:66px;height:14px;margin:0"></span>
      </div>
      <div class="twin-list">
        ${[0, 1]
          .map(
            () => `<div class="twin-card is-skeleton" aria-hidden="true">
          <div class="twin-thumb sk"></div>
          <div class="twin-body">
            <span class="sk sk-line lg w50"></span>
            <span class="sk sk-line w40"></span>
            <div class="twin-actions"><span class="sk sk-btn"></span></div>
          </div>
        </div>`
          )
          .join("")}
      </div>`;
    const statRow = $(".stat-row");
    if (statRow) statRow.classList.add("is-loading");
    ["stat-avatars", "stat-videos", "stat-reuse"].forEach((id) => {
      $("#" + id).innerHTML = `<span class="sk" style="display:block;width:34px;height:20px;margin:2px auto 6px"></span>`;
    });
    $("#resume-bar").hidden = true;
    $("#home-recent").hidden = true;
  }

  function syncTwinVideos() {
    $$("#avatar-zone .twin-thumb video").forEach((video) => {
      const card = video.closest("[data-avatar]");
      const active = card && card.classList.contains("is-active");
      if (state.screen === "home" && active) {
        const playing = video.play();
        if (playing && playing.catch) playing.catch(() => {});
      } else {
        video.pause();
      }
    });
  }

  function scheduleAvatarRefresh() {
    clearTimeout(state.avatarTimer);
    state.avatarTimer = setTimeout(() => {
      if (state.screen === "home") refreshAll();
    }, 4000);
  }

  function renderHome() {
    const zone = $("#avatar-zone");
    if (!zone) return;

    if (!state.loaded) {
      renderHomeSkeleton();
      return;
    }
    if (state.uploading) return; // the build card owns the zone

    const ready = state.avatars.filter((a) => a.status === "ready");
    const building = state.avatars.filter((a) => a.status === "processing");
    const failed = state.avatars.filter((a) => a.status === "failed");
    const hasAny = state.avatars.length > 0;
    $("#screen-home").classList.toggle("has-twins", hasAny);

    if (hasAny) {
      zone.innerHTML = `
        <div class="zone-head">
          <p class="zone-title">我的分身<span class="zone-count">${state.avatars.length}</span></p>
          <button class="zone-new" data-act="new">${IC("plus")}新建分身</button>
        </div>
        <div class="twin-list">
          ${ready.map((a) => twinCard(a, !!(state.avatar && state.avatar.id === a.id))).join("")}
          ${building.map((a) => buildingCard(a)).join("")}
          ${failed.map((a) => failedAvatarCard(a)).join("")}
        </div>`;
      bindTwinList();
      if (building.length) scheduleAvatarRefresh();
    } else {
      zone.innerHTML = uploadCard();
      bindUploadCard();
    }

    const statRow = $(".stat-row");
    if (statRow) statRow.classList.remove("is-loading");
    const totalReuse = state.avatars.reduce((sum, a) => sum + (a.reuse_count || 0), 0);
    $("#stat-avatars").textContent = ready.length;
    $("#stat-videos").textContent = state.taskStats.success || 0;
    $("#stat-reuse").textContent = totalReuse;

    // resume banner
    const lastId = localStorage.getItem("dh.lastTaskId");
    const last = state.tasks.find((t) => t.id === lastId);
    const banner = $("#resume-bar");
    if (last && (last.status === "processing" || last.status === "pending")) {
      banner.hidden = false;
      const pct = last.progress ? ` · ${Math.round(last.progress)}%` : "";
      $("#resume-text").textContent = `${last.stage_label || "正在生成"}${pct} · ${last.title || "口播视频"}`;
      banner.onclick = () => openTask(last.id);
    } else {
      banner.hidden = true;
    }

    // recent strip
    const recent = state.tasks.filter((t) => t.status === "success").slice(0, 6);
    const strip = $("#home-recent");
    strip.hidden = recent.length === 0;
    if (recent.length) {
      $("#home-recent-body").innerHTML = recent
        .map(
          (t) => `
        <button class="strip-item" data-task="${esc(t.id)}">
          <video src="${esc(t.video_url)}#t=0.1" muted playsinline preload="metadata"></video>
          <p>${esc(t.title || "口播成片")}</p>
        </button>`
        )
        .join("");
      $$("#home-recent-body .strip-item").forEach((el) =>
        el.addEventListener("click", () => openExistingTask(el.dataset.task))
      );
      markHScroll($("#home-recent-body"));
    }
  }

  function uploadCard() {
    return `
      <div class="upload" id="upload-box" role="button" tabindex="0" aria-label="选择视频，建立数字人分身">
        <div class="upload-orb">${IC("upload")}</div>
        <h3>建立你的数字人分身</h3>
        <p>上传一段 1–2 分钟真实口播视频<br/>自动抽音克隆 · 自动转 9:16 · 无需训练</p>
        <div class="meta-line"><span>mp4 / mov</span><span>单人正脸</span><span>一次性</span></div>
        <div class="upload-cta">${IC("plus")}选择视频</div>
      </div>`;
  }

  function twinCard(avatar, active) {
    const live = avatar.reuse_count > 0 ? `已复用 ${avatar.reuse_count} 次` : "尚未使用";
    return `
      <div class="twin-card ${active ? "is-active" : ""}" data-avatar="${esc(avatar.id)}">
        <div class="twin-thumb">
          <video src="${esc(avatar.preview_url)}#t=0.1" muted loop playsinline preload="metadata" ${active ? "autoplay" : ""}></video>
          <div class="twin-live"><i></i>READY</div>
        </div>
        <div class="twin-body">
          <div class="twin-title-row">
            <p class="twin-name">${esc(avatar.name)}</p>
            ${active ? '<span class="twin-tag">当前使用</span>' : ""}
            <button class="twin-more" data-act="menu" aria-label="管理分身 ${esc(avatar.name)}">${IC("more")}</button>
          </div>
          <p class="twin-meta">
            <span>${avatar.duration ? avatar.duration.toFixed(0) + "s 素材" : "素材就绪"}</span>
            <i></i><b>${live}</b>
          </p>
          <div class="twin-actions">
            <button class="twin-cta ${active ? "" : "is-soft"}" data-act="use">使用此分身创作 ${IC("chevron")}</button>
          </div>
        </div>
      </div>`;
  }

  function buildingCard(avatar) {
    return `
      <div class="twin-card is-building" data-avatar="${esc(avatar.id)}">
        <div class="twin-thumb"><div class="twin-ph"><span class="spinner"></span></div></div>
        <div class="twin-body">
          <p class="twin-name">${esc(avatar.name)}</p>
          <p class="twin-meta"><span>正在建立分身</span><i></i><span>约 20–60 秒</span></p>
          <p class="twin-note">完成后即可用它创作</p>
        </div>
      </div>`;
  }

  function failedAvatarCard(avatar) {
    return `
      <div class="twin-card is-failed" data-avatar="${esc(avatar.id)}">
        <div class="twin-thumb"><div class="twin-ph is-fail">${IC("close")}</div></div>
        <div class="twin-body">
          <p class="twin-name">${esc(avatar.name)}</p>
          <p class="twin-note">${esc(avatar.error || "创建失败，请重新上传")}</p>
          <div class="twin-actions">
            <button class="twin-cta is-soft" data-act="new">重新上传</button>
            <button class="twin-link" data-act="forget">删除</button>
          </div>
        </div>
      </div>`;
  }

  function focusAvatarCard(avatarId) {
    const card = avatarId ? $(`#avatar-zone [data-avatar="${avatarId}"]`) : null;
    const target = card ? card.querySelector('[data-act="use"]') || card : null;
    const fallback = target || $(".zone-new") || $("#upload-box");
    if (fallback) fallback.focus({ preventScroll: true });
  }

  function selectAvatar(avatar) {
    if (!avatar || avatar.status !== "ready") return;
    const changed = !state.avatar || state.avatar.id !== avatar.id;
    state.avatar = avatar;
    localStorage.setItem("dh.avatarId", avatar.id);
    if (changed) toast(`已切换到「${avatar.name}」`);
    renderHome();
    showScreen("create");
  }

  function buildCard({ status, stage, progress, error }) {
    const steps = [
      ["audio", "抽取干净人声"],
      ["clone", "MiniMax 声音复刻"],
      ["transcode", "转码 9:16 竖屏"],
      ["uploading", "上传分身资产"],
    ];
    const order = ["uploaded", "audio", "clone", "transcode", "uploading", "done"];
    const index = Math.max(0, order.indexOf(stage));
    const failed = status === "failed";
    const done = status === "success" || stage === "done";

    return `
      <div class="build-card">
        <div class="build-head">
          <h3>${failed ? "分身创建失败" : done ? "分身已就绪" : "正在建立分身"}</h3>
          <span>${failed ? "—" : Math.min(100, progress || 0) + "%"}</span>
        </div>
        <div class="build-bar"><i style="width:${failed ? 100 : Math.min(100, progress || 0)}%"></i></div>
        <ul class="build-steps">
          ${steps
            .map(([key, label], i) => {
              const stepIndex = i + 1;
              const isDone = done || index > stepIndex;
              const isActive = !failed && !done && index === stepIndex;
              return `<li class="${isDone ? "is-done" : isActive ? "is-active" : ""}">
                <span class="dot">${IC("check")}</span>
                <span>${label}</span>
                <em>${isDone ? "完成" : isActive ? "进行中" : "等待"}</em>
              </li>`;
            })
            .join("")}
        </ul>
        ${
          failed
            ? `<div class="build-error"><p>${esc(error || "创建失败，请重试")}</p></div>`
            : `<p class="build-log">整个过程约 20–60 秒，完成后可无限次复用。</p>`
        }
        ${
          failed
            ? `<div class="error-actions">
                 <button class="ghost-wide" data-act="reset">重新上传</button>
               </div>`
            : ""
        }
        ${
          done
            ? `<div class="error-actions"><button class="primary small" data-act="create">开始创作</button></div>`
            : ""
        }
      </div>`;
  }

  function bindUploadCard() {
    const box = $("#upload-box");
    if (!box) return;
    box.addEventListener("click", () => $("#file-input").click());
    box.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        $("#file-input").click();
      }
    });
    box.addEventListener("dragover", (e) => {
      e.preventDefault();
      box.classList.add("is-hover");
    });
    box.addEventListener("dragleave", () => box.classList.remove("is-hover"));
    box.addEventListener("drop", (e) => {
      e.preventDefault();
      box.classList.remove("is-hover");
      const file = e.dataTransfer.files && e.dataTransfer.files[0];
      if (file) startUpload(file);
    });
  }

  function bindTwinList() {
    $$("#avatar-zone [data-act]").forEach((btn) => {
      btn.addEventListener("click", (event) => {
        event.stopPropagation();
        const card = btn.closest("[data-avatar]");
        const avatar = card ? state.avatars.find((a) => a.id === card.dataset.avatar) : null;
        const act = btn.dataset.act;
        if (act === "new") {
          $("#file-input").click();
        } else if (act === "use" && avatar) {
          selectAvatar(avatar);
        } else if (act === "menu" && avatar) {
          openAvatarMenu(avatar);
        } else if (act === "forget" && avatar) {
          askDeleteAvatar(avatar);
        }
      });
    });
  }

  function bindBuildActions() {
    $$("#avatar-zone [data-act]").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (btn.dataset.act === "reset") {
          renderHome();
          setTimeout(() => $("#file-input").click(), 120);
        } else if (btn.dataset.act === "create") {
          showScreen("create");
        }
      });
    });
  }

  /* ---------------------------------------------------------------- upload */
  function startUpload(file) {
    if (state.uploading) return;
    if (!/^video\//.test(file.type) && !/\.(mp4|mov|m4v|avi|mkv|webm)$/i.test(file.name)) {
      toast("请选择视频文件（mp4 / mov）", "err");
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      toast("视频超过 500MB，请压缩后再试", "err");
      return;
    }

    state.uploading = true;
    const zone = $("#avatar-zone");
    zone.innerHTML = buildCard({ status: "processing", stage: "uploaded", progress: 2 });
    const bar = $(".build-bar i", zone);
    const head = $(".build-head span", zone);

    const form = new FormData();
    form.append("file", file);
    form.append("name", state.avatars.length ? `分身 ${state.avatars.length + 1}` : "我的数字人");

    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/setup_video");
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const pct = Math.max(2, Math.round((event.loaded / event.total) * 100));
      if (bar) bar.style.width = `${pct}%`;
      if (head) head.textContent = `${pct}%`;
      const log = $(".build-log", zone);
      if (log) {
        log.textContent = event.loaded >= event.total
          ? "上传完成，正在抽音…"
          : `正在上传 ${file.name} · ${pct}%`;
      }
    };
    xhr.onload = () => {
      let data = null;
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        data = null;
      }
      if (xhr.status >= 400) {
        const detail = data && data.detail;
        failUpload(typeof detail === "string" ? detail : (detail && detail.message) || "上传失败");
        return;
      }
      state.uploading = false;
      localStorage.setItem("dh.avatarId", data.avatar_id);
      pollSetup(data.job_id, data.avatar_id);
    };
    xhr.onerror = () => failUpload("网络异常，上传失败");
    xhr.send(form);

    function failUpload(message) {
      state.uploading = false;
      if (state.avatars.length) {
        refreshAll();
        toast(message, "err", "", null, 4200);
        return;
      }
      zone.innerHTML = buildCard({ status: "failed", stage: "failed", progress: 100, error: message });
      bindBuildActions();
      toast(message, "err", "", null, 4200);
    }
  }

  function pollSetup(jobId, avatarId) {
    clearInterval(state.setupTimer);
    const started = Date.now();
    const tick = async () => {
      try {
        const job = await api(`/api/setup_video/${jobId}`);
        const zone = $("#avatar-zone");
        if (state.screen === "home" && zone) {
          zone.innerHTML = buildCard({
            status: job.status,
            stage: job.stage,
            progress: job.progress,
            error: job.error,
          });
          bindBuildActions();
          const log = $(".build-log", zone);
          if (log && job.logs && job.logs.length) log.textContent = job.logs[job.logs.length - 1].message;
        }
        if (job.status === "success") {
          clearInterval(state.setupTimer);
          localStorage.setItem("dh.avatarId", avatarId);
          toast("分身已就绪");
          await refreshAll();
          focusAvatarCard(avatarId);
          return;
        }
        if (job.status === "failed") {
          clearInterval(state.setupTimer);
          toast(job.error || "分身创建失败", "err", "", null, 4200);
          refreshAll();
          return;
        }
        if (Date.now() - started > 15 * 60 * 1000) {
          clearInterval(state.setupTimer);
          toast("创建超时，请检查后端日志", "err");
        }
      } catch (error) {
        clearInterval(state.setupTimer);
        toast(error.message, "err");
      }
    };
    tick();
    state.setupTimer = setInterval(tick, 1400);
  }

  /* --------------------------------------------------------------- compose */
  const IDEAS = {
    career: "很多人以为，职场里最重要的是能力。但我观察了身边升得最快的人，发现他们都做对了一件事：把每一次汇报，都当成一次产品发布。先说结论，再给证据，最后留一个明确的下一步。你的领导不需要你努力，他需要你可靠。",
    product: "如果你也经常熬夜，第二天还要见人，这条一定要看完。我最近在用的这个方法，不靠咖啡，也不靠意志力：睡前九十分钟，把屏幕亮度降到最低，再用温水泡脚十分钟。坚持一周，你会发现起床这件事，突然没那么难了。",
    knowledge: "为什么你学了很多道理，却依然过不好这一生？因为知识不是收藏品，是工具。一个真正有用的知识，必须能回答三个问题：它在什么场景下成立？它的边界在哪里？我明天可以怎么用它？想清楚这三个问题，你才算真正学会。",
    vlog: "今天想和你分享一个很小的瞬间。下班路上，我路过一家开了二十年的面馆，老板还记得我不吃香菜。那一刻我突然觉得，所谓生活的确定性，大概就是有人记得你的偏好。愿你也被这样温柔地记住。",
  };

  const AI_TEMPLATES = [
    "先说一个反常识的结论：{topic}，真正决定结果的，往往不是努力程度，而是你选择在哪件事上用力。接下来三分钟，我用三个身边真实的例子，告诉你应该怎么判断。",
    "如果你最近也在为{topic}发愁，先别急着找答案。今天我想分享一个更底层的思路：把问题拆小，把动作变具体，把反馈周期缩短。做到这三点，你会发现事情开始向前走了。",
    "关于{topic}，我踩过最大的一个坑，就是总想一步到位。后来我才明白，完成比完美重要，迭代比计划重要。这条视频，送给同样在路上的你。",
  ];

  function renderCompose() {
    const avatar = state.avatar && state.avatar.status === "ready" ? state.avatar : state.avatars.find((a) => a.status === "ready");
    const chip = $("#twin-chip");
    if (!avatar) {
      chip.innerHTML = `<div class="dot-avatar">?</div><span>还没有分身，先上传一段视频</span>`;
      chip.onclick = () => showScreen("home");
      $("#btn-generate").disabled = true;
    } else {
      chip.innerHTML = `
        <div class="dot-avatar"><video src="${esc(avatar.preview_url)}#t=0.1" muted playsinline preload="metadata"></video></div>
        <div><b>${esc(avatar.name)}</b><span>${esc((avatar.voice_id || "").slice(0, 12))}</span></div>
        <em>切换</em>
        ${IC("chevron")}`;
      chip.onclick = () => showScreen("home");
    }
    $("#voice-sub").textContent = avatar ? "seed-icl-2.0 · 相似度 97.5%" : "创建分身后可试听";
    syncOptions();
    updateComposeState();

    const saved = localStorage.getItem("dh.draft");
    if (saved && !$("#script").value) {
      $("#script").value = saved;
      $("#script").dispatchEvent(new Event("input"));
      const hint = $("#draft-hint");
      if (saved.trim()) { hint.textContent = "已恢复草稿"; hint.classList.add("is-visible"); }
    }
  }

  /* 字幕 / AI 标识开关：默认都关闭，选择记在 localStorage */
  const OPT_KEYS = { subtitles: "dh.opt.subtitles", label: "dh.opt.label" };

  function optOn(name) {
    return localStorage.getItem(OPT_KEYS[name]) === "1";
  }

  function applyOpt(name, on) {
    localStorage.setItem(OPT_KEYS[name], on ? "1" : "0");
    const el = $(name === "subtitles" ? "#opt-subtitles" : "#opt-label");
    if (el) el.setAttribute("aria-checked", on ? "true" : "false");
  }

  function toggleOpt(name) {
    applyOpt(name, !optOn(name));
  }

  function syncOptions() {
    $("#opt-subtitles").setAttribute("aria-checked", optOn("subtitles") ? "true" : "false");
    $("#opt-label").setAttribute("aria-checked", optOn("label") ? "true" : "false");
  }

  function updateComposeState() {
    const text = $("#script").value;
    const chars = countChars(text);
    const seconds = estimate(text);
    $("#est-time").textContent = `约 ${seconds} 秒`;
    $("#hint-duration").textContent = `${seconds}s`;
    const ready = state.avatars.some((a) => a.status === "ready");
    $("#btn-generate").disabled = chars < 2 || chars > MAX_CHARS || !ready;
    const counter = $("#char-count");
    counter.classList.toggle("is-over", chars >= MAX_CHARS);
    counter.classList.toggle("is-long", chars > COMFORT_CHARS && chars < MAX_CHARS);
    counter.textContent = chars > COMFORT_CHARS ? `${chars} / ${MAX_CHARS} 字` : `${chars} 字`;
    localStorage.setItem("dh.draft", text);
    clearTimeout(state.draftTimer);
    const hint = $("#draft-hint");
    hint.classList.remove("is-visible");
    if (text.trim()) {
      state.draftTimer = setTimeout(() => {
        hint.textContent = "已自动保存";
        hint.classList.add("is-visible");
        setTimeout(() => hint.classList.remove("is-visible"), 1400);
      }, 800);
    }
  }

  function fillIdea(key) {
    const script = $("#script");
    script.value = IDEAS[key] || "";
    updateComposeState();
    script.focus();
    $$("#ideas .idea").forEach((btn) => {
      const active = btn.dataset.idea === key;
      btn.classList.toggle("is-active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    toast("已填入灵感模板，可直接改写");
  }

  function runAi() {
    if (state.aiBusy) return;
    const script = $("#script");
    const base = script.value.trim();
    if (!base) {
      script.value = IDEAS.knowledge;
      updateComposeState();
      toast("已生成一条示例，继续点可按主题改写");
      return;
    }
    state.aiBusy = true;
    const button = $("#btn-ai");
    const previous = button.innerHTML;
    button.classList.add("is-busy");
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span>生成中`;
    setTimeout(() => {
      const topic = base.slice(0, 14);
      const template = AI_TEMPLATES[Math.floor(Math.random() * AI_TEMPLATES.length)];
      script.value = template.replace("{topic}", topic);
      updateComposeState();
      state.aiBusy = false;
      button.classList.remove("is-busy");
      button.disabled = false;
      button.innerHTML = previous;
      script.focus();
      toast("已按你的主题改写，可继续调整");
    }, 620);
  }

  async function tryVoice() {
    const avatar = state.avatar;
    if (!avatar || avatar.status !== "ready") {
      toast("请先创建分身", "err");
      return;
    }
    const audio = $("#preview-audio");
    if (state.previewPlaying) {
      stopPreview();
      return;
    }
    const button = $("#btn-try");
    const orb = $("#voice-orb");
    const previous = button.innerHTML;
    button.classList.add("is-busy");
    button.disabled = true;
    button.innerHTML = `<span class="spinner"></span><span>合成中</span>`;
    $("#voice-sub").textContent = "正在合成试听…";
    try {
      const text = ($("#script").value.trim() || "你好，我是你的数字人分身，这条口播由我替你出镜。").slice(0, 40);
      const data = await api("/api/tts", {
        method: "POST",
        body: { text, avatar_id: avatar.id },
      });
      audio.src = data.audio_url;
      audio.currentTime = 0;
      await audio.play();
      state.previewPlaying = true;
      orb.classList.add("playing");
      button.innerHTML = `${IC("pause")}<span>暂停</span>`;
      $("#voice-sub").textContent = `试听音色 · ${data.duration.toFixed(1)}s`;
    } catch (error) {
      toast(error.message, "err");
      $("#voice-sub").textContent = "试听失败，请重试";
    } finally {
      button.classList.remove("is-busy");
      button.disabled = false;
      button.innerHTML = state.previewPlaying ? `${IC("pause")}<span>暂停</span>` : previous;
    }
  }

  function stopPreview() {
    const audio = $("#preview-audio");
    audio.pause();
    state.previewPlaying = false;
    $("#voice-orb").classList.remove("playing");
    $("#btn-try").innerHTML = `${IC("play")}<span>试听</span>`;
  }

  async function generate() {
    if (state.generating) return;
    const text = $("#script").value.trim();
    if (countChars(text) < 2) {
      toast("先写点文案吧", "err");
      return;
    }
    if (!state.avatar || state.avatar.status !== "ready") {
      toast("请先创建分身", "err");
      showScreen("home");
      return;
    }
    stopPreview();
    state.generating = true;
    const button = $("#btn-generate");
    const label = button.querySelector("span");
    const original = label.textContent;
    button.disabled = true;
    button.classList.add("is-busy");
    label.innerHTML = `<span class="spinner"></span>提交中…`;
    try {
      const data = await api("/api/generate", {
        method: "POST",
        body: {
          text,
          avatar_id: state.avatar.id,
          title: text.slice(0, 18),
          subtitles: optOn("subtitles"),
          ai_label: optOn("label"),
        },
      });
      localStorage.setItem("dh.lastTaskId", data.task_id);
      announce("已提交生成任务，正在后台处理");
      openTask(data.task_id);
      refreshAll();
    } catch (error) {
      toast(error.message, "err");
    } finally {
      state.generating = false;
      button.disabled = false;
      button.classList.remove("is-busy");
      label.textContent = original;
    }
  }

  /* -------------------------------------------------------------- progress */
  function stopTaskPolling() {
    clearInterval(state.pollTimer);
    clearInterval(state.elapsedTimer);
    state.pollTimer = null;
    state.elapsedTimer = null;
  }

  async function openExistingTask(taskId) {
    try {
      const task = await api(`/api/task/${taskId}`);
      if (task.status === "success") openResult(task);
      else if (task.status === "failed") {
        openTask(taskId);
      } else openTask(taskId);
    } catch (error) {
      toast(error.message, "err");
    }
  }

  function openTask(taskId) {
    showScreen("progress");
    state.startedAt = Date.now();
    $("#error-card").hidden = true;
    setRing(0, "排队中");
    $("#progress-log").textContent = "建立任务…";
    $("#progress-topbar-title").textContent = "生成中";
    $("#progress-title").textContent = "正在为你合成";
    $("#progress-sub").textContent = "全程约 1–3 分钟，可退出后台运行";
    $("#progress-elapsed").textContent = "00:00";

    state.taskStartedAt = Date.now();
    state.lastAnnounced = "";
    stopTaskPolling();
    state.elapsedTimer = setInterval(() => {
      const seconds = Math.floor((Date.now() - (state.taskStartedAt || state.startedAt)) / 1000);
      $("#progress-elapsed").textContent = fmtClock(seconds);
    }, 1000);

    const tick = async () => {
      try {
        const task = await api(`/api/task/${taskId}`);
        state.task = task;
        state.pollErrors = 0;
        if (task.created_at) state.taskStartedAt = task.created_at * 1000;
        renderProgress(task);
        if (task.status === "success") {
          stopTaskPolling();
          setRing(100, "已完成");
          $("#progress-topbar-title").textContent = "已完成";
          localStorage.setItem("dh.lastTaskId", "");
          if (navigator.vibrate) navigator.vibrate(12);
          toast("成片已生成");
          await refreshAll();
          await new Promise((resolve) => setTimeout(resolve, 420));
          openResult(task);
        } else if (task.status === "failed") {
          stopTaskPolling();
          showProgressError(task.error || "生成失败");
        }
      } catch (error) {
        state.pollErrors = (state.pollErrors || 0) + 1;
        if (state.pollErrors === 3) {
          $("#progress-log").textContent = "网络波动，正在自动重试…";
          toast("网络不稳定，已自动重试", "err");
        }
        console.warn(error);
      }
    };
    tick();
    state.pollTimer = setInterval(tick, 1300);
  }

  const STAGE_ORDER = ["pending", "tts", "driving", "subtitle", "done"];

  function renderProgress(task) {
    const progress = Math.max(0, Math.min(100, task.progress || 0));
    setRing(progress, task.stage_label || "");
    $("#progress-log").textContent =
      (task.logs && task.logs.length && task.logs[task.logs.length - 1].message) || "处理中…";
    const ring = $("#ring");
    if (ring) ring.setAttribute("aria-valuenow", String(Math.round(progress)));
    const live = $("#log-live");
    if (live) {
      const running = task.status === "processing" || task.status === "pending";
      live.classList.toggle("is-idle", !running);
      live.lastChild.textContent = running ? "LIVE" : "LOG";
    }

    let current = STAGE_ORDER.indexOf(task.stage);
    if (task.status === "failed" && task.failed_stage) {
      current = STAGE_ORDER.indexOf(task.failed_stage);
    }
    if (current < 1 && task.status !== "success" && task.status !== "failed") current = 1;
    $$("#steps li").forEach((li) => {
      const step = li.dataset.step;
      const index = STAGE_ORDER.indexOf(step);
      const done = current > index || task.status === "success";
      const active = current === index && task.status !== "success" && task.status !== "failed";
      li.classList.toggle("is-done", done);
      li.classList.toggle("is-active", active);
      li.classList.toggle("is-fail", task.status === "failed" && index === current);
      const em = li.querySelector("em");
      if (em)
        em.textContent = done
          ? "完成"
          : active
            ? "进行中"
            : task.status === "failed" && index === current
              ? "失败"
              : task.status === "failed"
                ? "未完成"
                : "待开始";
    });

    if (task.stage === "tts") {
      $("#progress-title").textContent = "正在复刻你的声音";
      $("#progress-sub").textContent = "MiniMax 语音合成 · 逐字输出";
    } else if (task.stage === "driving") {
      $("#progress-title").textContent = "分身正在对口型";
      $("#progress-sub").textContent = "腾讯智影视频免训练 · 无需等待训练";
    } else if (task.stage === "subtitle") {
      $("#progress-title").textContent = "正在压制字幕";
      $("#progress-sub").textContent = "9:16 竖屏 · H.264 封装";
    } else if (task.status === "success") {
      $("#progress-title").textContent = "成片已完成";
    }
    const label = task.status === "success" ? "成片已生成" : task.stage_label || "";
    if (label && label !== state.lastAnnounced) {
      state.lastAnnounced = label;
      announce(task.status === "success" ? "成片已生成，正在打开" : `进入阶段：${label}`);
    }
  }

  function setRing(percent, label, animate = true) {
    const circumference = 2 * Math.PI * 62;
    const value = Math.max(0, Math.min(100, percent));
    const from = state.ringValue || 0;
    state.ringValue = value;
    if (label !== undefined) $("#ring-stage").textContent = label || "";
    const ring = $("#ring-value");
    const text = $("#ring-percent");
    const aria = $("#ring");
    if (aria) aria.setAttribute("aria-valuenow", String(Math.round(value)));
    const apply = (v) => {
      if (ring) {
        ring.style.strokeDasharray = `${circumference}`;
        ring.style.strokeDashoffset = `${circumference * (1 - v / 100)}`;
      }
      text.innerHTML = `${Math.round(v)}<span>%</span>`;
    };
    if (!animate || from === value || window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      apply(value);
      return;
    }
    const started = performance.now();
    const duration = 420;
    const tick = (now) => {
      const t = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      apply(from + (value - from) * eased);
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  }

  function showProgressError(message) {
    $("#progress-topbar-title").textContent = "生成失败";
    $("#error-card").hidden = false;
    $("#error-msg").textContent = message;
    $("#progress-title").textContent = "差一点就完成了";
    $("#progress-sub").textContent = "可以返回修改文案后重试";
    $$("#steps li").forEach((li) => li.classList.remove("is-active"));
    setTimeout(() => {
      const screen = $("#screen-progress");
      const card = $("#error-card");
      const offset = card.getBoundingClientRect().top - screen.getBoundingClientRect().top + screen.scrollTop;
      screen.scrollTo({ top: Math.max(0, offset - 76), behavior: "smooth" });
    }, 120);
  }

  /* ---------------------------------------------------------------- result */
  async function openResult(task) {
    state.task = task;
    stopTaskPolling();
    showScreen("result");
    $("#result-title").textContent = task.title || "口播成片";
    $("#result-sub").textContent = `生成于 ${fmtAgo(task.created_at)} · ${task.duration ? task.duration.toFixed(1) + "s" : ""}`;
    const avatar = state.avatars.find((a) => a.id === task.avatar_id);
    $("#result-tags").innerHTML = `
      <span>${avatar && avatar.reuse_count ? `分身第 ${avatar.reuse_count} 次复用` : "复用分身"}</span>
      <span>9:16 · 1080p</span>
      <span class="neutral">${avatar ? esc(avatar.name) : "数字人"}</span>`;

    const subsBurned = !!task.subtitles_burned;
    const subsRequested =
      task.subtitles_requested === undefined ? subsBurned : !!task.subtitles_requested;
    const labelBurned = !!task.label_burned;
    const compliance = $("#result-compliance");
    if (compliance) {
      compliance.textContent = labelBurned
        ? "成片含「AI 生成」标识 · 智影云端资源保留 7 天，请及时下载归档"
        : "成片不含「AI 生成」标识，发布前请按平台规则自行声明 · 智影云端资源保留 7 天，请及时下载归档";
    }

    const video = $("#video");
    video.src = task.video_url;
    video.load();
    video.muted = false;
    setPlayerIcon(false);
    $("#player").classList.remove("is-playing");
    $("#player").classList.remove("controls-hidden");
    $("#seek").value = 0;
    $("#seek").style.setProperty("--p", "0%");
    $("#time-now").textContent = "0:00";
    $("#time-total").textContent = "0:00";
    $("#btn-mute").innerHTML = IC("volume");
    $("#player-caption").textContent = "";
    state.cues = [];
    // 只有「选了字幕但没烧进去」才用网页字幕兜底，避免无字幕成片在预览里看起来有字幕
    if (task.srt_url && subsRequested && !subsBurned) loadSubtitles(task.srt_url);
  }

  /* player: keyboard + auto-hide controls while playing */
  let playerHideTimer = null;
  function scheduleControlsHide() {
    const player = $("#player");
    clearTimeout(playerHideTimer);
    if (!$("#video").paused) {
      playerHideTimer = setTimeout(() => player.classList.add("controls-hidden"), 2600);
    }
  }
  function showControls() {
    $("#player").classList.remove("controls-hidden");
    scheduleControlsHide();
  }
  function bindPlayerExtras() {
    const player = $("#player");
    player.addEventListener("click", (event) => {
      if (event.target.closest("button")) return;
      togglePlay();
    });
    player.addEventListener("mousemove", showControls);
    player.addEventListener("touchstart", showControls, { passive: true });
    player.addEventListener("pointerdown", showControls);
    player.addEventListener("keydown", (event) => {
      const video = $("#video");
      if (event.key === " " || event.key === "k") { event.preventDefault(); togglePlay(); }
      else if (event.key === "m") { $("#btn-mute").click(); }
      else if (event.key === "ArrowRight") { if (video.duration) video.currentTime = Math.min(video.duration, video.currentTime + 5); }
      else if (event.key === "ArrowLeft") { video.currentTime = Math.max(0, video.currentTime - 5); }
      else if (event.key === "ArrowUp") { event.preventDefault(); video.volume = Math.min(1, video.volume + 0.1); }
      else if (event.key === "ArrowDown") { event.preventDefault(); video.volume = Math.max(0, video.volume - 0.1); }
      else if (event.key === "f") { toggleFullscreen(); }
    });
  }
  function toggleFullscreen() {
    const player = $("#player");
    if (document.fullscreenElement) document.exitFullscreen?.();
    else player.requestFullscreen?.();
  }

  async function loadSubtitles(url) {
    try {
      const response = await fetch(url);
      const text = await response.text();
      state.cues = parseSrt(text);
    } catch {
      state.cues = [];
    }
  }

  function parseSrt(raw) {
    const cues = [];
    const blocks = (raw || "").replace(/\r/g, "").trim().split(/\n\s*\n/);
    const pattern = /(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})/;
    for (const block of blocks) {
      const match = pattern.exec(block);
      if (!match) continue;
      const n = match.slice(1).map(Number);
      const start = n[0] * 3600 + n[1] * 60 + n[2] + n[3] / 1000;
      const end = n[4] * 3600 + n[5] * 60 + n[6] + n[7] / 1000;
      const body = block.slice(match.index + match[0].length).trim().replace(/\n/g, " ");
      if (body && body !== "AI 生成") cues.push({ start, end, text: body });
    }
    return cues;
  }

  function setPlayerIcon(playing) {
    $("#btn-play").innerHTML = IC(playing ? "pause" : "play");
    $("#btn-big").innerHTML = IC(playing ? "pause" : "play");
  }

  function togglePlay() {
    const video = $("#video");
    if (!video.src) return;
    if (video.paused) {
      video.play().catch(() => toast("浏览器阻止了自动播放，请再点一次"));
    } else {
      video.pause();
    }
  }

  function updateCaption(time) {
    const caption = $("#player-caption");
    if (!state.cues.length) return;
    let current = null;
    for (const cue of state.cues) {
      if (time >= cue.start && time <= cue.end) {
        current = cue;
        break;
      }
    }
    const text = current ? current.text : "";
    if (caption.textContent !== text) caption.textContent = text;
  }

  /* ---------------------------------------------------------------- sheets */
  let sheetReturnFocus = null;
  function setShellInert(on) {
    const shell = $(".shell");
    if (shell) shell.toggleAttribute("inert", on);
    document.body.classList.toggle("sheet-open", on);
  }

  function openSheet(title, html) {
    closeToast();
    if (!sheetReturnFocus) sheetReturnFocus = document.activeElement;
    $("#sheet-title").textContent = title;
    $("#sheet-body").innerHTML = html;
    $("#overlay").hidden = false;
    setShellInert(true);
    const sheet = $("#sheet");
    sheet.style.transform = "";
    sheet.classList.remove("is-dragging");
    requestAnimationFrame(() => {
      $("#overlay").classList.add("is-open");
      setTimeout(() => $("#sheet-close").focus({ preventScroll: true }), 120);
    });
  }

  function closeSheet(after, options = {}) {
    if ($("#overlay").hidden) {
      if (typeof after === "function") after();
      return;
    }
    const sheet = $("#sheet");
    sheet.style.transition = "transform 0.28s var(--ease)";
    sheet.style.transform = "translateY(100%)";
    setTimeout(() => {
      $("#overlay").classList.remove("is-open");
      setTimeout(() => {
        $("#overlay").hidden = true;
        sheet.style.transition = "";
        sheet.style.transform = "";
        setShellInert(false);
        const back = sheetReturnFocus;
        sheetReturnFocus = null;
        if (options.restoreFocus !== false && back && document.contains(back)) {
          back.focus({ preventScroll: true });
        }
        if (typeof after === "function") after();
      }, 300);
    }, 80);
  }

  function trapSheetFocus(event) {
    if (event.key !== "Tab" || $("#overlay").hidden) return;
    const focusable = $$(
      'button:not([hidden]):not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      $("#sheet")
    ).filter((el) => el.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  let sheetDrag = null;
  function bindSheetDrag() {
    const overlay = $("#overlay");
    const sheet = $("#sheet");
    const handle = sheet;
    const start = (event) => {
      if ($("#overlay").hidden) return;
      if (event.target.closest(".sheet-body")) return;
      const point = event.touches ? event.touches[0] : event;
      sheetDrag = { y: point.clientY, base: sheet.getBoundingClientRect().top, moved: false };
      sheet.classList.add("is-dragging");
      sheet.style.transition = "none";
      window.addEventListener("pointermove", move);
      window.addEventListener("pointerup", end);
    };
    const move = (event) => {
      if (!sheetDrag) return;
      const point = event.touches ? event.touches[0] : event;
      const delta = Math.max(0, point.clientY - sheetDrag.y);
      sheet.style.transform = `translateY(${delta}px)`;
      if (delta > 6) sheetDrag.moved = true;
    };
    const end = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", end);
      if (!sheetDrag) return;
      const top = sheet.getBoundingClientRect().top;
      const moved = sheetDrag.moved;
      sheetDrag = null;
      sheet.classList.remove("is-dragging");
      sheet.style.transition = "transform 0.28s var(--ease)";
      if (moved && top > window.innerHeight * 0.55) {
        closeSheet();
      } else {
        sheet.style.transform = "";
      }
    };
    handle.addEventListener("pointerdown", start);
  }

  function confirmSheet(title, message, confirmText, onConfirm, options = {}) {
    openSheet(
      title,
      `<p class="confirm-text">${esc(message)}</p>
       <div class="error-actions">
         <button class="ghost-wide" id="confirm-cancel">取消</button>
         <button class="primary small ${options.danger ? "is-danger" : ""}" id="confirm-ok">${esc(confirmText)}</button>
       </div>`
    );
    $("#confirm-cancel").onclick = () => closeSheet();
    $("#confirm-ok").onclick = () => closeSheet(onConfirm);
  }

  function openAvatarMenu(avatar) {
    openSheet(
      "分身设置",
      `<div class="menu-list">
         <button class="menu-row" id="menu-rename">
           <span class="menu-main"><b>重命名</b><small>当前名称：${esc(avatar.name)}</small></span>
           ${IC("chevron")}
         </button>
         <button class="menu-row is-danger" id="menu-delete">
           <span class="menu-main"><b>删除分身</b><small>已生成的作品会保留</small></span>
         </button>
       </div>`
    );
    $("#menu-rename").onclick = () => openRenameSheet(avatar);
    $("#menu-delete").onclick = () => closeSheet(() => askDeleteAvatar(avatar));
  }

  function openRenameSheet(avatar) {
    openSheet(
      "重命名分身",
      `<input class="sheet-input" id="rename-input" maxlength="24" value="${esc(avatar.name)}" aria-label="分身名称" />
       <div class="error-actions">
         <button class="ghost-wide" id="rename-cancel">取消</button>
         <button class="primary small" id="rename-save">保存</button>
       </div>`
    );
    const input = $("#rename-input");
    const save = $("#rename-save");
    setTimeout(() => {
      input.focus();
      input.select();
    }, 260);

    const commit = async () => {
      const name = input.value.trim();
      if (!name) {
        toast("名称不能为空", "err");
        input.focus();
        return;
      }
      if (name === avatar.name) {
        closeSheet();
        return;
      }
      save.disabled = true;
      save.innerHTML = `<span class="spinner"></span>保存中`;
      try {
        await api(`/api/avatars/${avatar.id}`, { method: "PATCH", body: { name } });
        closeSheet(async () => {
          await refreshAll();
          focusAvatarCard(avatar.id);
          toast("分身已重命名");
        });
      } catch (error) {
        toast(error.message, "err");
        save.disabled = false;
        save.textContent = "保存";
      }
    };

    save.onclick = commit;
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        commit();
      }
    });
    $("#rename-cancel").onclick = () => closeSheet();
  }

  function askDeleteAvatar(avatar) {
    const wasActive = !!(state.avatar && state.avatar.id === avatar.id);
    confirmSheet(
      "删除这个分身？",
      `「${avatar.name}」删除后无法恢复，已生成的作品会保留。`,
      "删除",
      async () => {
        try {
          await api(`/api/avatars/${avatar.id}`, { method: "DELETE" });
          if (wasActive) {
            state.avatar = null;
            localStorage.removeItem("dh.avatarId");
          }
          await refreshAll();
          focusAvatarCard(state.avatar && state.avatar.id);
          toast("分身已删除");
        } catch (error) {
          toast(error.message, "err");
        }
      },
      { danger: true }
    );
  }

  function askDeleteTask(task) {
    if (!task) return;
    if (task.status !== "success" && task.status !== "failed") {
      toast("这条还在生成，完成后再删除");
      return;
    }
    confirmSheet(
      "删除这条作品？",
      `「${task.title || "未命名口播"}」删除后无法恢复，成片与字幕文件会一起清理。`,
      "删除",
      async () => {
        try {
          await api(`/api/task/${task.id}`, { method: "DELETE" });
          if (state.task && state.task.id === task.id) state.task = null;
          toast("作品已删除");
          loadWorksPage(state.worksPage || 1);
        } catch (error) {
          toast(error.message, "err");
        }
      },
      { danger: true }
    );
  }

  function worksSkeleton(count = 4) {
    return Array.from({ length: count })
      .map(
        () => `<div class="sk-work" aria-hidden="true">
          <span class="sk sk-thumb"></span>
          <span class="sk-copy"><span class="sk sk-line w70"></span><span class="sk sk-line w40"></span></span>
          <span class="sk" style="width:40px;height:18px;border-radius:4px"></span>
        </div>`
      )
      .join("");
  }

  function workRow(task) {
    const status =
      task.status === "success"
        ? `<span class="work-status ok">成片</span>`
        : task.status === "failed"
          ? `<span class="work-status err">失败</span>`
          : `<span class="work-status run">${esc(task.stage_label || "生成中")}</span>`;
    const thumb =
      task.status === "success" && task.video_url
        ? `<video src="${esc(task.video_url)}#t=0.1" muted playsinline preload="metadata"></video>`
        : IC("video");
    return `<div class="work-row">
      <button class="work-item" data-task="${esc(task.id)}">
        <div class="work-thumb">${thumb}</div>
        <div class="work-info">
          <b>${esc(task.title || "未命名口播")}</b>
          <span>${fmtAgo(task.created_at)} · ${task.duration ? task.duration.toFixed(1) + "s" : "—"}</span>
        </div>
        ${status}
      </button>
      <button class="work-del" data-del="${esc(task.id)}" aria-label="删除作品">${IC("trash")}</button>
    </div>`;
  }

  async function loadWorksPage(page = 1) {
    const list = $("#works-list");
    if (!list) return;
    list.innerHTML = worksSkeleton(4);
    $("#works-pager").hidden = true;
    $("#works-total").textContent = "—";
    try {
      const data = await api(`/api/tasks?page=${page}&page_size=${WORKS_PAGE_SIZE}`);
      const items = data.items || [];
      const current = data.page || 1;
      const pages = data.pages || 1;
      state.worksPage = current;
      state.worksPages = pages;
      const okCount = (data.counts && data.counts.success) || 0;
      $("#works-total").textContent = data.total
        ? okCount === data.total
          ? `${data.total} 条`
          : `${data.total} 条（${okCount} 成片）`
        : "0 条";

      if (!items.length) {
        list.innerHTML = `<div class="empty-state works-empty">
          <div class="empty-icon">${IC("video")}</div>
          <p>还没有作品，第一条只要一段文案。</p>
          <button class="primary" id="works-empty-cta">去做第一条</button>
        </div>`;
        const cta = $("#works-empty-cta");
        if (cta) {
          cta.onclick = () =>
            showScreen(state.avatars.some((a) => a.status === "ready") ? "create" : "home");
        }
        return;
      }

      list.innerHTML = items.map(workRow).join("");
      $$("#works-list .work-item").forEach((el) =>
        el.addEventListener("click", () => openExistingTask(el.dataset.task))
      );
      $$("#works-list .work-del").forEach((el) => {
        const task = items.find((item) => item.id === el.dataset.del);
        el.addEventListener("click", (event) => {
          event.stopPropagation();
          askDeleteTask(task);
        });
      });

      const pager = $("#works-pager");
      pager.hidden = pages <= 1;
      $("#works-page-info").textContent = `${current} / ${pages}`;
      $("#works-prev").disabled = current <= 1;
      $("#works-next").disabled = current >= pages;
    } catch (error) {
      list.innerHTML = `<div class="sheet-empty">${esc(error.message)}</div>`;
    }
  }

  /* ----------------------------------------------------------------- share */
  async function downloadVideo() {
    const task = state.task;
    if (!task || !task.download_url) return;
    const button = $("#btn-download");
    const label = button.querySelector("span");
    const original = label.textContent;
    button.classList.add("is-busy");
    button.setAttribute("aria-busy", "true");
    label.innerHTML = `<span class="spinner"></span>下载中`;
    try {
      const response = await fetch(task.download_url);
      if (!response.ok) throw new Error("下载失败，请重试");
      const total = Number(response.headers.get("content-length")) || 0;
      let blob;
      if (response.body && total > 0) {
        const reader = response.body.getReader();
        const chunks = [];
        let received = 0;
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          chunks.push(value);
          received += value.length;
          const pct = Math.min(99, Math.round((received / total) * 100));
          label.innerHTML = `<span class="spinner"></span>${pct}%`;
        }
        blob = new Blob(chunks, { type: "video/mp4" });
      } else {
        blob = await response.blob();
      }
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${task.title || "digital-human"}.mp4`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      if (navigator.vibrate) navigator.vibrate(10);
      toast("已保存到本地，请及时备份");
    } catch (error) {
      toast(error.message || "下载失败", "err");
    } finally {
      button.classList.remove("is-busy");
      button.removeAttribute("aria-busy");
      label.textContent = original;
    }
  }

  async function shareVideo() {
    const task = state.task;
    if (!task) return;
    const button = $("#btn-share");
    const label = button.querySelector("span");
    const original = label.textContent;
    button.classList.add("is-busy");
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    label.innerHTML = `<span class="spinner"></span>准备中`;
    try {
      const timeout = new Promise((resolve) => setTimeout(() => resolve("timeout"), 20000));
      const result = await Promise.race([doShare(task), timeout]);
      if (result === "timeout") toast("分享面板未响应，可先下载视频再发布");
    } finally {
      button.classList.remove("is-busy");
      button.disabled = false;
      button.removeAttribute("aria-busy");
      label.textContent = original;
    }
  }

  async function doShare(task) {
    const caption = `${task.title || "口播成片"}\n用数字人分身生成，文案即视频。#数字人 #口播 #AI创作`;
    const absolute = task.video_url ? new URL(task.video_url, location.origin).href : location.href;
    try {
      const response = await fetch(absolute);
      const blob = await response.blob();
      const file = new File([blob], `${task.title || "digital-human"}.mp4`, { type: "video/mp4" });
      if (navigator.canShare && navigator.canShare({ files: [file] })) {
        await navigator.share({ files: [file], text: caption });
        return;
      }
      if (navigator.share) {
        await navigator.share({ title: task.title || "口播成片", text: caption, url: absolute });
        return;
      }
    } catch (error) {
      if (error && error.name === "AbortError") return;
    }
    try {
      await navigator.clipboard.writeText(`${caption}\n${absolute}`);
      toast("文案已复制，正在打开小红书创作中心");
    } catch {
      toast("请先下载视频，再发布到小红书");
    }
    window.open("https://creator.xiaohongshu.com/publish/publish", "_blank", "noopener");
  }

  /* ------------------------------------------------------------------ boot */
  function bindEvents() {
    bindPlayerExtras();
    bindSheetDrag();
    ["subtitles", "label"].forEach((name) => {
      const el = $(name === "subtitles" ? "#opt-subtitles" : "#opt-label");
      if (el) el.addEventListener("click", () => toggleOpt(name));
    });
    syncOptions();
    $$(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        if (tab.dataset.tab === "create" && !state.avatars.some((a) => a.status === "ready")) {
          toast("请先建立数字人分身", "err");
          showScreen("home");
          return;
        }
        showScreen(tab.dataset.tab);
      });
    });

    $$("[data-back]").forEach((btn) => {
      btn.addEventListener("click", () => showScreen(btn.dataset.back));
    });

    $("#btn-progress-back").addEventListener("click", () => {
      goHome();
      toast("任务仍在后台生成");
    });

    ["#btn-works-home", "#btn-works-home2", "#btn-works-create", "#btn-works-result"].forEach((sel) => {
      const el = $(sel);
      if (el) el.addEventListener("click", () => showScreen("works"));
    });
    $("#works-prev").addEventListener("click", () =>
      loadWorksPage(Math.max(1, (state.worksPage || 1) - 1))
    );
    $("#works-next").addEventListener("click", () =>
      loadWorksPage(Math.min(state.worksPages || 1, (state.worksPage || 1) + 1))
    );

    const autoGrow = () => {
      const script = $("#script");
      script.style.height = "auto";
      script.style.height = `${Math.min(script.scrollHeight, 220)}px`;
    };
    $("#script").addEventListener("input", () => {
      autoGrow();
      updateComposeState();
      $$("#ideas .idea.is-active").forEach((btn) => {
        btn.classList.remove("is-active");
        btn.setAttribute("aria-pressed", "false");
      });
    });
    $("#btn-clear").addEventListener("click", () => {
      const previous = $("#script").value;
      if (!previous.trim()) return;
      $("#script").value = "";
      updateComposeState();
      $("#script").focus();
      toast("已清空文案", "", "撤销", () => {
        $("#script").value = previous;
        updateComposeState();
        $("#script").focus();
      }, 5000);
    });
    $("#script").addEventListener("keydown", (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
        event.preventDefault();
        if (!$("#btn-generate").disabled) generate();
      }
    });
    markHScroll($("#ideas"));
    $("#btn-ai").addEventListener("click", runAi);
    $$("#ideas .idea").forEach((btn) =>
      btn.addEventListener("click", () => fillIdea(btn.dataset.idea))
    );
    $("#btn-try").addEventListener("click", tryVoice);
    $("#btn-generate").addEventListener("click", generate);
    $("#preview-audio").addEventListener("ended", stopPreview);

    $("#file-input").addEventListener("change", (event) => {
      const file = event.target.files && event.target.files[0];
      event.target.value = "";
      if (file) startUpload(file);
    });

    // drop anywhere on the page
    let dragDepth = 0;
    window.addEventListener("dragenter", (event) => {
      if (!state.avatars.some((a) => a.status === "ready")) {
        dragDepth += 1;
        $("#drop-veil").classList.add("show");
      }
    });
    window.addEventListener("dragleave", () => {
      dragDepth = Math.max(0, dragDepth - 1);
      if (dragDepth === 0) $("#drop-veil").classList.remove("show");
    });
    window.addEventListener("dragover", (event) => event.preventDefault());
    window.addEventListener("drop", (event) => {
      event.preventDefault();
      dragDepth = 0;
      $("#drop-veil").classList.remove("show");
      const file = event.dataTransfer && event.dataTransfer.files && event.dataTransfer.files[0];
      if (file && !state.avatars.some((a) => a.status === "ready")) {
        showScreen("home");
        startUpload(file);
      }
    });

    $("#btn-error-back").addEventListener("click", () => {
      showScreen("create");
    });
    $("#btn-retry").addEventListener("click", async () => {
      const task = state.task;
      if (!task) return;
      const button = $("#btn-retry");
      const original = button.textContent;
      button.classList.add("is-busy");
      button.disabled = true;
      button.innerHTML = `<span class="spinner"></span>重试中`;
      $("#script").value = task.text || $("#script").value;
      updateComposeState();
      try {
        await generate();
      } finally {
        button.classList.remove("is-busy");
        button.disabled = false;
        button.textContent = original;
      }
    });

    // player
    $("#btn-big").addEventListener("click", togglePlay);
    $("#btn-play").addEventListener("click", togglePlay);
    const video = $("#video");
    video.addEventListener("play", () => {
      setPlayerIcon(true);
      $("#player").classList.add("is-playing");
      scheduleControlsHide();
    });
    video.addEventListener("pause", () => {
      setPlayerIcon(false);
      $("#player").classList.remove("is-playing");
      $("#player").classList.remove("controls-hidden");
      clearTimeout(playerHideTimer);
    });
    video.addEventListener("seeked", showControls);
    video.addEventListener("loadedmetadata", () => {
      $("#time-total").textContent = fmtTime(video.duration);
    });
    video.addEventListener("timeupdate", () => {
      const ratio = video.duration ? video.currentTime / video.duration : 0;
      const seek = $("#seek");
      seek.value = Math.round(ratio * 1000);
      seek.style.setProperty("--p", `${ratio * 100}%`);
      $("#time-now").textContent = fmtTime(video.currentTime);
      updateCaption(video.currentTime);
    });
    video.addEventListener("ended", () => {
      $("#player").classList.remove("is-playing");
      setPlayerIcon(false);
      $("#player-caption").textContent = "";
    });
    $("#seek").addEventListener("input", (event) => {
      const ratio = Number(event.target.value) / 1000;
      if (video.duration) video.currentTime = ratio * video.duration;
      event.target.style.setProperty("--p", `${ratio * 100}%`);
    });
    $("#btn-mute").addEventListener("click", () => {
      video.muted = !video.muted;
      $("#btn-mute").innerHTML = IC(video.muted ? "mute" : "volume");
    });

    $("#btn-download").addEventListener("click", downloadVideo);
    $("#btn-share").addEventListener("click", shareVideo);
    $("#btn-again").addEventListener("click", () => {
      stopPreview();
      const previous = (state.task && state.task.text) || "";
      $("#script").value = previous;
      updateComposeState();
      showScreen("create");
      setTimeout(() => {
        const script = $("#script");
        script.focus();
        script.setSelectionRange(script.value.length, script.value.length);
      }, 380);
      if (previous) toast("已带入上一条文案，可直接修改");
    });

    // sheets
    $("#sheet-close").addEventListener("click", closeSheet);
    $("#overlay").addEventListener("click", (event) => {
      if (event.target === $("#overlay")) closeSheet();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !$("#overlay").hidden) closeSheet();
      trapSheetFocus(event);
    });

    window.addEventListener("popstate", (event) => {
      if (!$("#overlay").hidden) closeSheet();
      const name = (event.state && event.state.screen) || "home";
      if (name === "home" || name === "create") {
        if (state.task && state.task.status !== "success" && state.task.status !== "failed") {
          toast("任务仍在后台生成，可回首页继续查看");
        }
        stopTaskPolling();
      }
      showScreen(name, { history: "none" });
      if (name === "home") refreshAll();
    });
  }

  async function boot() {
    bindEvents();
    history.replaceState({ screen: "home" }, "");
    renderHomeSkeleton();
    try {
      const health = await api("/api/health");
      if (health.mock) {
        document.documentElement.dataset.mock = "1";
        $(".brand-sub").textContent = "MOCK 演示";
      }
    } catch {
      /* health is advisory */
    }
    await refreshAll();

    const lastId = localStorage.getItem("dh.lastTaskId");
    if (lastId) {
      try {
        const task = await api(`/api/task/${lastId}`);
        if (task.status === "processing" || task.status === "pending") {
          $("#resume-bar").hidden = false;
        } else if (task.status === "success") {
          localStorage.setItem("dh.lastTaskId", "");
        }
      } catch {
        localStorage.setItem("dh.lastTaskId", "");
      }
    }
    showScreen("home", { history: "none" });
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
