# Portals and experiences

A **portal** is a published map with its own URL. You pick the layers, arrange them, choose how the
page is laid out, and publish. Visitors need nothing installed.

## The three experiences

Choosing an experience changes the *shape of the page*, not just its colours.

=== "Web map"

    Map-first, with a layer list beside it. The default, and the right choice when the map itself is
    the point.

    - Full-screen map
    - Layer list you can dock left or right, or float over the map
    - Folders, legend, basemap switcher, About panel

=== "Story map"

    Scrollytelling. The page is a narrative of sections; each one is pinned to a map position and a
    set of visible layers, and the map animates as the reader scrolls.

    - Write each section in the editor
    - Capture the map camera for a section as you go
    - The layer list starts collapsed, so the story leads

=== "Catalog"

    A browsing surface, for when you have more datasets than a single map should show at once. The
    dataset list *is* the page and the map sits beside it.

    - Search across names, descriptions and keywords
    - Filter by folder, type and licence
    - Each result shows what it is, and how to get it
    - Click a dataset to add it to the map, or zoom to its extent

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

Templates set the look — palette, typography, header treatment — and may preset an experience. You
can change any of it afterwards; a template is a starting point, not a constraint.

Per-portal branding (accent colour, font, logo, light/dark) is set in the editor and overrides the
template.
