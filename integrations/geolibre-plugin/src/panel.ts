/**
 * The plugin's right-panel UI (plain DOM — an external plugin can't share the
 * host's React). Lets the user set the GeoDeploy URL + API token, preview what
 * would import (dry run), and publish the current project as a portal.
 */
import type { GeoLibreAppAPI } from "./host-api";
import {
  type GeoDeploySettings,
  type PreviewResult,
  type PublishResult,
  canPublish,
  portalUrl,
  previewProject,
  publishProject,
} from "./publish";

export interface PanelContext {
  app: GeoLibreAppAPI;
  getSettings: () => GeoDeploySettings;
  setSettings: (patch: Partial<GeoDeploySettings>) => void;
}

const C = "gdp"; // class prefix, to scope the injected CSS

function el(tag: string, cls?: string, text?: string): HTMLElement {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

function field(label: string, input: HTMLElement): HTMLElement {
  const wrap = el("label", `${C}-field`);
  wrap.appendChild(el("span", `${C}-label`, label));
  wrap.appendChild(input);
  return wrap;
}

export function renderPublishPanel(container: HTMLElement, ctx: PanelContext): () => void {
  container.classList.add(`${C}-root`);
  container.innerHTML = "";

  const settings = ctx.getSettings();

  const urlInput = el("input", `${C}-input`) as HTMLInputElement;
  urlInput.type = "url";
  urlInput.placeholder = "https://geo.example.org";
  urlInput.value = settings.baseUrl || "";
  urlInput.addEventListener("input", () => ctx.setSettings({ baseUrl: urlInput.value }));

  const tokenInput = el("input", `${C}-input`) as HTMLInputElement;
  tokenInput.type = "password";
  tokenInput.placeholder = "GeoDeploy API token";
  tokenInput.value = settings.token || "";
  tokenInput.autocomplete = "off";
  tokenInput.addEventListener("input", () => ctx.setSettings({ token: tokenInput.value }));

  const previewBtn = el("button", `${C}-btn ${C}-btn-secondary`, "Preview") as HTMLButtonElement;
  const publishBtn = el("button", `${C}-btn ${C}-btn-primary`, "Publish to GeoDeploy") as HTMLButtonElement;
  const actions = el("div", `${C}-actions`);
  actions.appendChild(previewBtn);
  actions.appendChild(publishBtn);

  const status = el("div", `${C}-status`);
  const results = el("div", `${C}-results`);

  const intro = el(
    "p",
    `${C}-intro`,
    "Send the current project to a GeoDeploy instance as a hosted portal. " +
      "Layers, styles, 3D and the story map are translated on the GeoDeploy side.",
  );

  container.appendChild(intro);
  container.appendChild(field("GeoDeploy URL", urlInput));
  container.appendChild(field("API token", tokenInput));
  container.appendChild(actions);
  container.appendChild(status);
  container.appendChild(results);

  // If the host can't hand us the project, disable everything and explain why.
  if (!canPublish(ctx.app)) {
    previewBtn.disabled = true;
    publishBtn.disabled = true;
    setStatus(
      status,
      "error",
      "This GeoLibre build doesn't expose the project to plugins (needs the project-snapshot API). " +
        "Update GeoLibre, then reopen this panel.",
    );
  }

  function busy(on: boolean): void {
    previewBtn.disabled = on || !canPublish(ctx.app);
    publishBtn.disabled = on || !canPublish(ctx.app);
  }

  previewBtn.addEventListener("click", async () => {
    results.innerHTML = "";
    setStatus(status, "info", "Previewing…");
    busy(true);
    try {
      const res = await previewProject(ctx.app, ctx.getSettings());
      setStatus(status, "ok", `Preview: ${res.layers.length} layer(s) would import.`);
      renderPreview(results, res);
    } catch (err) {
      setStatus(status, "error", (err as Error).message);
    } finally {
      busy(false);
    }
  });

  publishBtn.addEventListener("click", async () => {
    results.innerHTML = "";
    setStatus(status, "info", "Publishing… (ingesting layers, then building the portal)");
    busy(true);
    try {
      const res = await publishProject(ctx.app, ctx.getSettings());
      renderPublishResult(status, results, ctx.getSettings(), res);
    } catch (err) {
      setStatus(status, "error", (err as Error).message);
    } finally {
      busy(false);
    }
  });

  return () => {
    container.innerHTML = "";
  };
}

function setStatus(node: HTMLElement, kind: "info" | "ok" | "error", msg: string): void {
  node.className = `${C}-status ${C}-status-${kind}`;
  node.textContent = msg;
}

function warningsList(warnings: string[]): HTMLElement | null {
  if (!warnings.length) return null;
  const box = el("div", `${C}-warnings`);
  box.appendChild(el("div", `${C}-warnings-head`, `${warnings.length} note(s):`));
  const ul = el("ul");
  for (const w of warnings) ul.appendChild(el("li", undefined, w));
  box.appendChild(ul);
  return box;
}

function renderPreview(node: HTMLElement, res: PreviewResult): void {
  const table = el("table", `${C}-table`);
  const head = el("tr");
  for (const h of ["Layer", "As", "Render"]) head.appendChild(el("th", undefined, h));
  table.appendChild(head);
  for (const l of res.layers) {
    const tr = el("tr");
    tr.appendChild(el("td", undefined, l.name));
    tr.appendChild(el("td", undefined, `${l.target}${l.has_z ? " · Z" : ""}`));
    tr.appendChild(el("td", undefined, l.render_mode));
    table.appendChild(tr);
  }
  node.appendChild(table);
  const w = warningsList(res.warnings);
  if (w) node.appendChild(w);
}

function renderPublishResult(
  status: HTMLElement,
  node: HTMLElement,
  settings: GeoDeploySettings,
  res: PublishResult,
): void {
  setStatus(
    status,
    "ok",
    `Published — ${res.layer_count} layer(s), ${res.ingesting} ingesting in the background.`,
  );
  const url = portalUrl(settings, res.slug);
  const link = el("a", `${C}-link`, url) as HTMLAnchorElement;
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  const p = el("p");
  p.appendChild(el("span", undefined, "Portal: "));
  p.appendChild(link);
  node.appendChild(p);
  node.appendChild(
    el(
      "p",
      `${C}-hint`,
      "Ingestion runs in the background; the portal finishes building shortly after this returns.",
    ),
  );
  const w = warningsList(res.warnings);
  if (w) node.appendChild(w);
}
