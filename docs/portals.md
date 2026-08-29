# Portals and experiences

A **portal** is a published map with its own URL. You pick the layers, arrange them, choose how the
page is laid out, and publish. Visitors need nothing installed.

## The four experiences

Choosing an experience changes the *shape of the page*, not just its colours. Each has its own
guide; this page covers what they share — building, layout, access and templates.

=== "Web map"

    Map-first, with a layer list beside it. The default, and the right choice when the map itself is
    the point.

    - Full-screen map
    - Layer list you can dock left or right, or float over the map
    - Folders, legend, basemap switcher, About panel

    See **[Web maps](web-maps.md)**.

=== "Story map"

    Scrollytelling. The page is a narrative of sections; each one is pinned to a map position and a
    set of visible layers, and the map animates as the reader scrolls.

    - Write each section in the editor
    - Capture the map camera for a section as you go
    - The layer list starts collapsed, so the story leads

    See **[Story maps](story-maps.md)**.

=== "Catalog"

    A browsing surface, for when you have more datasets than a single map should show at once. The
    dataset list *is* the page and the map sits beside it.

    - Search across names, descriptions and keywords
    - Filter by folder, type and licence
    - Each result shows what it is, and how to get it
    - Click a dataset to add it to the map, or zoom to its extent

    See **[Catalogs](catalogs.md)**.

=== "Dashboard"

    A grid of widgets that cross-filter each other, where the map is one widget among charts,
    numbers, lists and controls. For the questions a reader asks of the *data* rather than of the
    geography.

    - Indicators, gauges, charts, tables, a column profile, a scatter
    - Selectors and a search box that narrow everything at once
    - Clicking a chart or drawing on the map re-asks every other question on the page
    - Widgets can be pinned to the map, or docked into its control cluster

    See **[Dashboards](dashboards.md)**.

## Building a portal

1. **Portals ▸ New portal.** Give it a title.
2. **Add layers.** `+ Add` picks them one at a time; `Add all` brings in everything you can see.
3. **Organise them.** Drag layers into folders, reorder them, set colours and opacity. The top of
   the list draws on top of the map.
4. **Set the opening view.** Position the map how you want visitors to find it — including the 3D
   globe if you switch to it — then pin it as the start view.
5. **Write the About page** (optional). A rich-text editor with images; paste screenshots straight
   in. It becomes a documentation page linked from the portal.
6. **Publish.**

!!! note "Re-publishing"
    A published portal is a static bundle, so changes only appear once you publish again. The button
    reads **Re-publish** after the first time.

## Layout options

Beyond the experience, each portal exposes a few placement choices:

| Option | What it does |
| --- | --- |
| Layer list side | Left or right |
| Layer list mode | Docked beside the map, or floating over it |
| Start collapsed | Opens with the list closed |
| Controls corner | Which corner the map controls cluster in |
| Map side and width | *Catalog only* — which side the map takes, and how much |
| Datasets listed | *Catalog only* — this portal's layers, or every public layer on the instance |

## Who can see it

Set per portal, independently of workspace permissions:

| Access | Who gets in |
| --- | --- |
| **Public** | Anyone with the link |
| **Password** | Anyone with the link *and* the password |
| **Organization** | Any signed-in member of your workspace |
| **Owner** | The creator and administrators |

Anything other than *Public* is enforced on the server before the page is served, not in the browser.

## Map tools visitors get

Every published portal includes a basemap switcher, a 2D/3D globe toggle, zoom, home (returns to the
pinned start view), zoom-to-all-layers, **previous/next extent**, and a draw-a-box download.

### Download by area

Visitors can draw a box and download just that area, choosing a format per layer. The clip runs on
the server and is prepared in the background, so a large selection does not block the page.

## On phones

Published portals adapt: the layer list becomes a drawer that starts closed so the map is visible,
map controls stay reachable, and a catalog portal stacks vertically — filters on top, results below
them, map beneath.

## Templates

A **template** is a starting point you pick when you create a portal. It can carry three separate
things, and it is worth knowing which is which, because you can change any of them afterwards
without touching the others.

| A template may set | What that means | Change it later? |
|---|---|---|
| **Look** | Palette, typography, header treatment, basemap | Yes — per-portal branding overrides it |
| **Experience** | Which of the four archetypes the portal starts as | Yes — switch experience at any time |
| **Starting content** | A story map's sections, or a dashboard's widgets and their wiring | Yes — everything is editable afterwards |

### How a template and an experience relate

They are not the same choice, and a template does not lock one in.

- The **experience** (web map, story map, catalog, dashboard) decides the *shape of the page*.
- A **template** may say which experience it was designed for, and most declare only one — a story
  template applied to a catalog would style a layout nobody designed for it.
- Switching experience keeps your layers, your styling and your branding. What changes is the
  arrangement around them.

### What a template's content does when you apply it

A template ships with **no layer ids** — it cannot have any, because your layers did not exist when
it was written. When you choose one, its content is bound to *your* portal's layers: the first
suitable layer, the first field of the right type, successive rasters for successive raster widgets.

Every one of those guesses is editable. You correct a guess rather than fill in a blank.

Two rules keep this predictable:

- A template's content **seeds an empty portal only**. It never silently overwrites work.
- To deliberately start over, use **Reload template** — a button you have to press.

### Branding

Per-portal branding (accent colour, font, logo, light/dark) is set in the editor and overrides the
template, whichever experience you are in.
