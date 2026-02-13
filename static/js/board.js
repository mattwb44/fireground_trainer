(() => {
  const board = document.getElementById("board");
  const layer = document.getElementById("tokenLayer");
  const clearBtn = document.getElementById("clearTokens");

  if (!board || !layer) {
    return;
  }

  const STORAGE_PREFIX = "fg.board.state.v1.";
  const storageKey = STORAGE_PREFIX + (board.dataset.scenarioKey || "default");
  const DRAG_START_THRESHOLD_PX = 6;
  const CYCLE_SAME_SPOT_TOLERANCE_PX = 24;

  const state = {
    nextId: 1,
    tokens: [],
    selectedTool: null,
    activeId: null,
    draggingId: null,
    rotatingId: null,
    placingId: null,
    sizingState: null,
    pointerDown: null,
    cycleState: null,
    defaultTokenPx: 52,
  };

  function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
  }

  function findTokenById(id) {
    return state.tokens.find((token) => token.id === id) || null;
  }

  function toIntFromTokenId(id) {
    const match = String(id).match(/\d+/);
    return match ? Number(match[0]) : 0;
  }

  function computeNextId(tokens) {
    const maxId = tokens.reduce((maxVal, token) => Math.max(maxVal, toIntFromTokenId(token.id)), 0);
    return maxId + 1;
  }

  function saveState() {
    const payload = {
      tokens: state.tokens,
      defaultTokenPx: state.defaultTokenPx,
    };
    localStorage.setItem(storageKey, JSON.stringify(payload));
  }

  function loadState() {
    try {
      const raw = localStorage.getItem(storageKey);
      if (!raw) {
        state.nextId = 1;
        return;
      }

      const parsed = JSON.parse(raw);
      const loadedTokens = Array.isArray(parsed.tokens) ? parsed.tokens : [];

      state.tokens = loadedTokens
        .filter((token) => token && token.id && token.type)
        .map((token) => {
          const rawSize = Number.isFinite(Number(token.size)) ? Number(token.size) : 52;
          const rawScale = Number.isFinite(Number(token.scale)) ? Number(token.scale) : 1;
          return {
            id: String(token.id),
            type: String(token.type),
            x: Number.isFinite(Number(token.x)) ? Number(token.x) : 50,
            y: Number.isFinite(Number(token.y)) ? Number(token.y) : 50,
            rotation: Number.isFinite(Number(token.rotation)) ? Number(token.rotation) : 0,
            size: clamp(rawSize * rawScale, 24, 600),
            layer: Number.isFinite(Number(token.layer)) ? Number(token.layer) : 1,
            notes: typeof token.notes === "string" ? token.notes : "",
            status: typeof token.status === "string" ? token.status : "active",
          };
        });

      const loadedDefault = Number(parsed.defaultTokenPx);
      state.defaultTokenPx = Number.isFinite(loadedDefault) ? clamp(loadedDefault, 24, 600) : 52;
      state.nextId = computeNextId(state.tokens);
    } catch (_err) {
      state.tokens = [];
      state.defaultTokenPx = 52;
      state.nextId = 1;
    }
  }

  function getToolMap() {
    const mapping = {};
    document.querySelectorAll(".tokenbtn").forEach((btn) => {
      mapping[btn.dataset.type] = btn.dataset.src;
    });
    return mapping;
  }

  const toolSrcByType = getToolMap();

  function applyTokenSize(tokenEl, sizePx) {
    const clamped = clamp(sizePx, 24, 600);
    const img = tokenEl.querySelector(".token-img");
    img.style.width = clamped + "px";
    img.style.height = clamped + "px";
  }

  function applyTokenTransform(tokenEl, token) {
    const degrees = Number(token.rotation) || 0;
    tokenEl.querySelector(".token-inner").style.transform = `rotate(${degrees}deg)`;
  }

  function applyTokenPosition(tokenEl, token) {
    // left/top represent token center because .token uses transform: translate(-50%, -50%)
    tokenEl.style.left = token.x + "%";
    tokenEl.style.top = token.y + "%";
  }

  function applyTokenVisual(tokenEl, token) {
    tokenEl.dataset.id = token.id;
    tokenEl.dataset.type = token.type;
    applyTokenSize(tokenEl, token.size);
    applyTokenTransform(tokenEl, token);
    applyTokenPosition(tokenEl, token);
  }

  function setActiveTokenById(id) {
    state.activeId = id || null;
    layer.querySelectorAll(".token.selected").forEach((el) => el.classList.remove("selected"));
    if (!state.activeId) return;

    const activeEl = layer.querySelector(`.token[data-id="${state.activeId}"]`);
    const activeToken = findTokenById(state.activeId);
    if (!activeEl || !activeToken) return;
    activeEl.classList.add("selected");
  }

  function getCenterClientXY(token) {
    const rect = board.getBoundingClientRect();
    return {
      cx: rect.left + (token.x / 100) * rect.width,
      cy: rect.top + (token.y / 100) * rect.height,
    };
  }

  function getEffectiveHalfSizePx(token) {
    const size = Number(token.size) || 52;
    return size / 2;
  }

  function toPercentCoords(clientX, clientY, token) {
    const rect = board.getBoundingClientRect();
    const half = token ? getEffectiveHalfSizePx(token) : state.defaultTokenPx / 2;
    const minVisiblePx = Math.min(32, half);

    // Allow partial off-board movement while keeping some of the token visible.
    // left/top still represent token center due to translate(-50%, -50%).
    const minCenterX = minVisiblePx - half;
    const maxCenterX = rect.width + half - minVisiblePx;
    const minCenterY = minVisiblePx - half;
    const maxCenterY = rect.height + half - minVisiblePx;

    const xPx = clamp(clientX - rect.left, minCenterX, maxCenterX);
    const yPx = clamp(clientY - rect.top, minCenterY, maxCenterY);
    return {
      x: (xPx / rect.width) * 100,
      y: (yPx / rect.height) * 100,
    };
  }

  function distancePx(x1, y1, x2, y2) {
    return Math.hypot(x2 - x1, y2 - y1);
  }

  function getTokenIdsAtPoint(clientX, clientY) {
    const seen = new Set();
    const ids = [];
    const stack = document.elementsFromPoint(clientX, clientY);

    stack.forEach((el) => {
      const tokenEl = el.closest && el.closest(".token");
      if (!tokenEl || !layer.contains(tokenEl)) return;
      const id = tokenEl.dataset.id;
      if (!id || seen.has(id)) return;
      seen.add(id);
      ids.push(id);
    });

    return ids;
  }

  function cycleSelectAtPoint(clientX, clientY) {
    const ids = getTokenIdsAtPoint(clientX, clientY);
    if (ids.length === 0) {
      setActiveTokenById(null);
      state.cycleState = null;
      return;
    }

    const key = ids.join("|");
    let nextIndex = 0;
    if (ids.length > 1 && state.cycleState && state.cycleState.key === key) {
      const sameSpot =
        distancePx(state.cycleState.x, state.cycleState.y, clientX, clientY) <=
        CYCLE_SAME_SPOT_TOLERANCE_PX;
      if (sameSpot) {
        nextIndex = (state.cycleState.index + 1) % ids.length;
      }
    }

    setActiveTokenById(ids[nextIndex]);
    state.cycleState = { key, index: nextIndex, x: clientX, y: clientY };
  }

  function attachTokenHandlers(tokenEl) {
    tokenEl.addEventListener("pointerdown", (e) => {
      const tokenId = tokenEl.dataset.id;
      const token = findTokenById(tokenId);
      if (!token) return;

      if (e.target.classList.contains("token-del")) return;
      setActiveTokenById(tokenId);

      if (e.target.classList.contains("token-rot")) {
        state.rotatingId = tokenId;
        tokenEl.setPointerCapture(e.pointerId);
        e.preventDefault();
        return;
      }

      if (e.target.classList.contains("token-size")) {
        const { cx, cy } = getCenterClientXY(token);
        const startDist = Math.hypot(e.clientX - cx, e.clientY - cy) || 1;
        state.sizingState = {
          tokenId,
          startDist,
          startSize: Number(token.size) || 52,
        };
        tokenEl.setPointerCapture(e.pointerId);
        e.preventDefault();
        return;
      }

      state.pointerDown = {
        tokenId,
        startX: e.clientX,
        startY: e.clientY,
        offsetX: e.clientX - getCenterClientXY(token).cx,
        offsetY: e.clientY - getCenterClientXY(token).cy,
      };
      tokenEl.setPointerCapture(e.pointerId);
      e.preventDefault();
    });

    const delBtn = tokenEl.querySelector(".token-del");
    delBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      const tokenId = tokenEl.dataset.id;
      state.tokens = state.tokens.filter((token) => token.id !== tokenId);
      if (state.activeId === tokenId) state.activeId = null;
      tokenEl.remove();
      setActiveTokenById(state.activeId);
      saveState();
    });
  }

  function createTokenElement(token) {
    const tokenEl = document.createElement("div");
    tokenEl.className = "token";

    const inner = document.createElement("div");
    inner.className = "token-inner";

    const img = document.createElement("img");
    img.className = "token-img";
    img.src = toolSrcByType[token.type] || "";
    img.alt = token.type;

    const del = document.createElement("button");
    del.type = "button";
    del.className = "token-del";
    del.textContent = "✕";
    del.title = "Remove";

    const rot = document.createElement("div");
    rot.className = "token-rot";
    rot.textContent = "R";
    rot.title = "Drag to rotate";

    const size = document.createElement("div");
    size.className = "token-size";
    size.textContent = "<>";
    size.title = "Drag to resize";

    inner.appendChild(img);
    inner.appendChild(del);
    inner.appendChild(rot);
    inner.appendChild(size);
    tokenEl.appendChild(inner);

    applyTokenVisual(tokenEl, token);
    attachTokenHandlers(tokenEl);
    return tokenEl;
  }

  function addToken(type, x, y) {
    if (!toolSrcByType[type]) return null;
    const token = {
      id: `t${state.nextId++}`,
      type,
      x: Number.isFinite(x) ? x : 50,
      y: Number.isFinite(y) ? y : 50,
      rotation: 0,
      size: state.defaultTokenPx,
      layer: 1,
      notes: "",
      status: "active",
    };
    state.tokens.push(token);
    const tokenEl = createTokenElement(token);
    layer.appendChild(tokenEl);
    setActiveTokenById(token.id);
    saveState();
    return token;
  }

  function beginPlacing(e) {
    if (!state.selectedTool) return;
    const coords = toPercentCoords(e.clientX, e.clientY, null);
    const token = addToken(state.selectedTool.type, coords.x, coords.y);
    if (!token) return;

    state.placingId = token.id;
    const placingEl = layer.querySelector(`.token[data-id="${token.id}"]`);
    if (placingEl) placingEl.style.opacity = "0.85";

    window.addEventListener("pointermove", onPlacingMove);
    window.addEventListener("pointerup", onPlacingUp, { once: true });
  }

  function onPlacingMove(e) {
    if (!state.placingId) return;
    const token = findTokenById(state.placingId);
    const tokenEl = layer.querySelector(`.token[data-id="${state.placingId}"]`);
    if (!token || !tokenEl) return;

    const coords = toPercentCoords(e.clientX, e.clientY, token);
    token.x = coords.x;
    token.y = coords.y;
    applyTokenPosition(tokenEl, token);
    saveState();
  }

  function onPlacingUp() {
    if (!state.placingId) return;
    const tokenEl = layer.querySelector(`.token[data-id="${state.placingId}"]`);
    if (tokenEl) tokenEl.style.opacity = "1";
    setActiveTokenById(state.placingId);
    state.placingId = null;
    window.removeEventListener("pointermove", onPlacingMove);
    saveState();
  }

  function updateDragging(e) {
    if (!state.draggingId && state.pointerDown) {
      const moved = distancePx(
        state.pointerDown.startX,
        state.pointerDown.startY,
        e.clientX,
        e.clientY
      );
      if (moved >= DRAG_START_THRESHOLD_PX) {
        state.draggingId = state.pointerDown.tokenId;
      }
    }

    if (!state.draggingId) return false;
    const token = findTokenById(state.draggingId);
    const tokenEl = layer.querySelector(`.token[data-id="${state.draggingId}"]`);
    if (!token || !tokenEl) return false;

    const offsetX = state.pointerDown ? state.pointerDown.offsetX : 0;
    const offsetY = state.pointerDown ? state.pointerDown.offsetY : 0;
    const centerClientX = e.clientX - offsetX;
    const centerClientY = e.clientY - offsetY;
    const coords = toPercentCoords(centerClientX, centerClientY, token);
    token.x = coords.x;
    token.y = coords.y;
    applyTokenPosition(tokenEl, token);
    saveState();
    return true;
  }

  function updateRotating(e) {
    if (!state.rotatingId) return false;
    const token = findTokenById(state.rotatingId);
    const tokenEl = layer.querySelector(`.token[data-id="${state.rotatingId}"]`);
    if (!token || !tokenEl) return false;

    const { cx, cy } = getCenterClientXY(token);
    const dx = e.clientX - cx;
    const dy = e.clientY - cy;
    const radians = Math.atan2(dy, dx);
    let deg = Math.round((radians * 180) / Math.PI);
    const snap = 15;
    deg = Math.round(deg / snap) * snap;
    token.rotation = deg;
    applyTokenTransform(tokenEl, token);
    saveState();
    return true;
  }

  function updateSizing(e) {
    if (!state.sizingState) return false;
    const token = findTokenById(state.sizingState.tokenId);
    const tokenEl = layer.querySelector(`.token[data-id="${state.sizingState.tokenId}"]`);
    if (!token || !tokenEl) return false;

    const { cx, cy } = getCenterClientXY(token);
    const curDist = Math.hypot(e.clientX - cx, e.clientY - cy);
    const ratio = curDist / state.sizingState.startDist;
    token.size = clamp(state.sizingState.startSize * ratio, 24, 600);
    applyTokenSize(tokenEl, token.size);
    saveState();
    return true;
  }

  function clearInteractionStates() {
    state.draggingId = null;
    state.rotatingId = null;
    state.sizingState = null;
    state.pointerDown = null;
  }

  function hydrateBoardFromState() {
    layer.innerHTML = "";
    state.tokens.forEach((token) => {
      const tokenEl = createTokenElement(token);
      layer.appendChild(tokenEl);
    });
    state.nextId = computeNextId(state.tokens);
    setActiveTokenById(null);
  }

  function bindUiEvents() {
    document.querySelectorAll(".tokenbtn").forEach((btn) => {
      btn.addEventListener("pointerdown", (e) => {
        state.selectedTool = { src: btn.dataset.src, type: btn.dataset.type };
        beginPlacing(e);
      });
    });

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        state.tokens = [];
        state.activeId = null;
        state.placingId = null;
        clearInteractionStates();
        layer.innerHTML = "";
        saveState();
      });
    }

    layer.addEventListener("pointerdown", (e) => {
      if (!e.target.closest(".token")) {
        setActiveTokenById(null);
        state.cycleState = null;
      }
    });

    // Global listeners keep drag/rotate active even when the pointer leaves the board.
    window.addEventListener("pointermove", (e) => {
      if (updateSizing(e)) return;
      if (updateDragging(e)) return;
      updateRotating(e);
    });

    window.addEventListener("pointerup", (e) => {
      if (state.pointerDown && !state.draggingId && !state.rotatingId && !state.sizingState) {
        cycleSelectAtPoint(e.clientX, e.clientY);
      }
      clearInteractionStates();
    });
    window.addEventListener("pointercancel", clearInteractionStates);
  }

  loadState();
  hydrateBoardFromState();
  bindUiEvents();
})();
