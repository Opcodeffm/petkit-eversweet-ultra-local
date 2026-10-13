/*
 * Petkit Trinkclips – Dashboard-Karte für die lokale Eversweet-Ultra-Integration.
 *
 *   type: custom:petkit-drink-card
 *   entity: sensor.eversweet_ultra_letzter_trinkvorgang
 *   title: Trinkbrunnen        # optional
 *   items: 6                   # optional, Einträge in der Liste
 *   show_list: true            # optional
 *
 * Zeigt den Clip des letzten Trinkvorgangs (stumm, in Schleife) und darunter die
 * letzten Trinkvorgänge. Ein Tipp auf einen Eintrag spielt dessen Clip.
 * Clips liegen hinter der HA-Anmeldung; die Karte holt sich signierte Links.
 */

const TEXT = {
  de: {
    today: "Heute", yesterday: "Gestern", times: "×", noClip: "Noch kein Clip",
    noDrinks: "Noch keine Trinkvorgänge", waiting: "Clip wird noch übertragen …",
    missing: "Entität nicht gefunden",
  },
  en: {
    today: "Today", yesterday: "Yesterday", times: "×", noClip: "No clip yet",
    noDrinks: "No drinks yet", waiting: "Clip is still uploading …",
    missing: "Entity not found",
  },
};

class PetkitDrinkCard extends HTMLElement {
  static getStubConfig(hass) {
    const entity = Object.keys(hass.states).find(
      (id) => id.startsWith("sensor.") && hass.states[id].attributes.history !== undefined
        && hass.states[id].attributes.today_count !== undefined,
    );
    return { entity: entity || "" };
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity fehlt");
    this._config = { items: 6, show_list: true, ...config };
    this._signed = new Map();
    this._selected = null;
    this._lastNewest = null;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this._build();
  }

  getCardSize() {
    return this._config && this._config.show_list ? 7 : 5;
  }

  set hass(hass) {
    this._hass = hass;
    const st = hass.states[this._config.entity];
    const sig = st ? JSON.stringify([st.state, st.attributes.history, st.attributes.today_count]) : "-";
    if (sig === this._sig) return;
    this._sig = sig;
    this._update(st);
  }

  get _t() {
    const lang = (this._hass && (this._hass.locale?.language || this._hass.language)) || "de";
    return lang.startsWith("de") ? TEXT.de : TEXT.en;
  }

  _build() {
    this.shadowRoot.innerHTML = `
      <style>
        [hidden] { display: none !important; }
        ha-card { overflow: hidden; }
        .title { padding: 12px 16px 0; font-size: 1.1em; font-weight: 500; }
        .stage { position: relative; background: #000; aspect-ratio: 16 / 9; }
        .stage video, .stage img { position: absolute; inset: 0; width: 100%; height: 100%;
                                   object-fit: contain; display: block; }
        .empty { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
                 color: #bbb; font-size: 0.95em; text-align: center; padding: 0 16px; }
        .caption { position: absolute; left: 0; right: 0; bottom: 0; padding: 18px 14px 10px;
                   background: linear-gradient(transparent, rgba(0,0,0,.75)); color: #fff; }
        .caption .msg { font-size: 1.15em; font-weight: 500; }
        .caption .when { font-size: .85em; opacity: .85; }
        .summary { padding: 10px 16px 4px; color: var(--secondary-text-color); font-size: .9em; }
        ul { list-style: none; margin: 0; padding: 0 8px 8px; }
        li { display: grid; grid-template-columns: 22px 1fr auto; gap: 8px; align-items: center;
             padding: 8px; border-radius: 8px; cursor: default; }
        li.clip { cursor: pointer; }
        li.clip:hover { background: var(--secondary-background-color); }
        li.sel { background: var(--secondary-background-color); }
        li ha-icon { --mdc-icon-size: 20px; color: var(--secondary-text-color); }
        li.clip ha-icon { color: var(--primary-color); }
        li .who { color: var(--primary-text-color); }
        li .when { color: var(--secondary-text-color); font-size: .85em; }
        li .dur { font-variant-numeric: tabular-nums; color: var(--primary-text-color); }
        li.unknown .who { color: var(--secondary-text-color); font-style: italic; }
      </style>
      <ha-card>
        <div class="title" hidden></div>
        <div class="stage">
          <video muted autoplay loop playsinline preload="auto" hidden></video>
          <img hidden alt="">
          <div class="empty"></div>
          <div class="caption" hidden><div class="msg"></div><div class="when"></div></div>
        </div>
        <div class="summary" hidden></div>
        <ul></ul>
      </ha-card>`;
    const $ = (s) => this.shadowRoot.querySelector(s);
    this._el = {
      title: $(".title"), video: $("video"), img: $("img"), empty: $(".empty"), caption: $(".caption"),
      msg: $(".msg"), when: $(".caption .when"), summary: $(".summary"), list: $("ul"),
    };
    if (this._config.title) {
      this._el.title.textContent = this._config.title;
      this._el.title.hidden = false;
    }
  }

  _update(st) {
    const t = this._t;
    if (!st) {
      this._showEmpty(`${t.missing}: ${this._config.entity}`);
      return;
    }
    const history = st.attributes.history || [];
    const newest = history[0];
    // Neuer Trinkvorgang: automatisch dorthin springen.
    if (newest && newest.id !== this._lastNewest) {
      this._lastNewest = newest.id;
      this._selected = null;
    }
    const current = history.find((d) => d.id === this._selected) || newest;
    this._show(current);

    const count = st.attributes.today_count || 0;
    const secs = st.attributes.today_seconds || 0;
    this._el.summary.hidden = false;
    this._el.summary.textContent = `${t.today}: ${count}${t.times} · ${this._dur(secs)}`;

    const list = this._el.list;
    list.innerHTML = "";
    if (!this._config.show_list) return;
    for (const d of history.slice(0, this._config.items)) {
      const li = document.createElement("li");
      li.className = (d.clip ? "clip" : "") + (current && d.id === current.id ? " sel" : "")
        + (d.known === false ? " unknown" : "");
      li.innerHTML = `<ha-icon icon="${d.clip ? "mdi:play-circle" : "mdi:cup-water"}"></ha-icon>
        <div><div class="who"></div><div class="when"></div></div><div class="dur"></div>`;
      li.querySelector(".who").textContent = d.pet || "";
      li.querySelector(".when").textContent = this._when(d.start);
      li.querySelector(".dur").textContent = this._dur(d.duration);
      if (d.clip) {
        li.addEventListener("click", () => {
          this._selected = d.id;
          this._sig = null;
          this._update(this._hass.states[this._config.entity]);
        });
      }
      list.appendChild(li);
    }
  }

  async _show(drink) {
    const t = this._t;
    const { video, img, caption, msg, when } = this._el;
    if (!drink) {
      this._showEmpty(t.noDrinks);
      return;
    }
    caption.hidden = false;
    msg.textContent = drink.message || "";
    when.textContent = this._when(drink.start);
    const token = (this._showToken = Symbol("show"));
    if (drink.clip) {
      const [src, poster] = await Promise.all([this._sign(drink.clip), drink.thumb ? this._sign(drink.thumb) : null]);
      if (token !== this._showToken) return;
      img.hidden = true;
      this._el.empty.textContent = "";
      if (video.dataset.clip !== drink.clip) {
        video.dataset.clip = drink.clip;
        if (poster) video.poster = poster; else video.removeAttribute("poster");
        video.src = src;
        video.play().catch(() => {});
      }
      video.hidden = false;
    } else {
      video.hidden = true;
      video.removeAttribute("src");
      delete video.dataset.clip;
      if (drink.thumb) {
        const src = await this._sign(drink.thumb);
        if (token !== this._showToken) return;
        img.src = src;
        img.hidden = false;
        this._el.empty.textContent = "";
      } else {
        img.hidden = true;
        this._el.empty.textContent = this._recent(drink.start) ? t.waiting : t.noClip;
      }
    }
  }

  _showEmpty(text) {
    const { video, img, caption } = this._el;
    video.hidden = true;
    img.hidden = true;
    caption.hidden = true;
    this._el.empty.textContent = text;
  }

  async _sign(path) {
    const hit = this._signed.get(path);
    if (hit && hit.until > Date.now()) return hit.url;
    const res = await this._hass.callWS({ type: "auth/sign_path", path, expires: 86400 });
    this._signed.set(path, { url: res.path, until: Date.now() + 23 * 3600 * 1000 });
    return res.path;
  }

  _recent(iso) {
    return iso && Date.now() - new Date(iso).getTime() < 15 * 60 * 1000;
  }

  _dur(seconds) {
    const s = Math.round(seconds || 0);
    const m = Math.floor(s / 60);
    const r = s % 60;
    if (!m) return `${r} s`;
    return r ? `${m} min ${r} s` : `${m} min`;
  }

  _when(iso) {
    if (!iso) return "";
    const t = this._t;
    const d = new Date(iso);
    const lang = this._hass?.locale?.language || this._hass?.language || "de";
    const tz = this._hass?.config?.time_zone;
    const opt = tz ? { timeZone: tz } : {};
    const time = d.toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit", ...opt });
    const day = (x) => x.toLocaleDateString("en-CA", opt);
    const now = new Date();
    if (day(d) === day(now)) return `${t.today} ${time}`;
    if (day(d) === day(new Date(now.getTime() - 86400000))) return `${t.yesterday} ${time}`;
    return `${d.toLocaleDateString(lang, { weekday: "short", day: "2-digit", month: "2-digit", ...opt })} ${time}`;
  }
}

if (!customElements.get("petkit-drink-card")) {
  customElements.define("petkit-drink-card", PetkitDrinkCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "petkit-drink-card",
    name: "Petkit Trinkclips",
    description: "Letzter Trinkclip des Eversweet Ultra und Übersicht der Trinkvorgänge",
    preview: false,
  });
}
