import "./media-preview.css";

document.addEventListener("click", (event) => {
  const trigger = event.target.closest?.("button[data-media-preview]");
  if (!trigger) return;
  const group = trigger.closest("[data-media-group]");
  const items = group ? [...group.querySelectorAll("button[data-media-preview]")] : [trigger];
  let index = items.indexOf(trigger);
  const viewer = document.createElement("dialog");
  viewer.className = "media-viewer";
  viewer.setAttribute("aria-label", "素材预览");
  viewer.innerHTML = `<div class="media-tools">
    <span aria-live="polite"></span>
    <button type="button" data-size aria-pressed="false">原尺寸</button>
    <button type="button" data-prev aria-label="上一项">‹</button>
    <button type="button" data-next aria-label="下一项">›</button>
    <button type="button" data-close aria-label="关闭预览" autofocus>×</button>
  </div><div class="media-stage"></div>`;
  const stage = viewer.querySelector(".media-stage");
  const size = viewer.querySelector("[data-size]");
  const previous = viewer.querySelector("[data-prev]");
  const next = viewer.querySelector("[data-next]");
  previous.hidden = next.hidden = items.length === 1;

  function clearMedia() {
    const video = stage.querySelector("video");
    if (video) {
      video.onerror = null;
      video.pause();
      video.removeAttribute("src");
      video.load();
    }
    stage.replaceChildren();
  }

  function show(position) {
    clearMedia();
    index = (position + items.length) % items.length;
    const item = items[index];
    const thumb = item.querySelector("img");
    const video = item.dataset.mediaPreview === "video";
    const media = document.createElement(video ? "video" : "img");
    const label = item.getAttribute("aria-label");
    viewer.querySelector("[aria-live]").textContent =
      items.length > 1 ? `${label} · ${index + 1} / ${items.length}` : label;
    size.hidden = video;
    size.setAttribute("aria-pressed", "false");
    stage.classList.remove("actual-size");
    stage.scrollTo(0, 0);
    if (video) {
      media.controls = true;
      media.playsInline = true;
      media.autoplay = true;
      if (thumb) media.poster = thumb.src;
      media.setAttribute("aria-label", label);
    } else {
      media.alt = thumb?.alt || label;
      media.referrerPolicy = "no-referrer";
    }
    media.onerror = () => {
      if (media.parentNode !== stage) return;
      if (!video && thumb && media.src !== thumb.src) {
        media.src = thumb.src;
        return;
      }
      size.hidden = true;
      const message = document.createElement("span");
      message.setAttribute("role", "status");
      message.textContent = video ? "视频暂时无法播放" : "图片暂时无法加载";
      if (video) {
        const link = document.createElement("a");
        link.href = item.dataset.source;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "打开视频来源 ↗";
        message.append(" · ", link);
      }
      stage.replaceChildren(message);
    };
    stage.append(media);
    media.src = item.dataset.source || thumb.src;
  }

  const close = () => viewer.close();
  size.onclick = () => {
    const actual = stage.classList.toggle("actual-size");
    size.setAttribute("aria-pressed", String(actual));
  };
  previous.onclick = () => show(index - 1);
  next.onclick = () => show(index + 1);
  viewer.querySelector("[data-close]").onclick = close;
  viewer.onclick = (e) => { if (e.target === viewer) close(); };
  viewer.onkeydown = (e) => {
    if (items.length === 1 || e.target.closest("video")) return;
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      show(index + (e.key === "ArrowLeft" ? -1 : 1));
    }
  };
  viewer.onclose = () => {
    clearMedia();
    window.removeEventListener("hashchange", close);
    viewer.remove();
    if (trigger.isConnected) trigger.focus({ preventScroll: true });
  };
  document.body.append(viewer);
  viewer.showModal();
  show(index);
  window.addEventListener("hashchange", close);
});
