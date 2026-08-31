---
description: >-
  Publish a searchable geospatial data catalog: browse and filter layers by metadata, preview them on a map, and download or connect to them over open standards.
---

# Catalogs

A **catalog** is a browsing surface, for when you have more datasets than a single map should show
at once. The dataset list *is* the page; the map sits beside it and shows whatever the reader
switches on.

Choose it when your visitors arrive looking for *a* dataset rather than looking at *the* map.

---

## The page

<div class="gd-tiles" markdown>

<figure markdown>
![Catalog portal](assets/portal-catalog.png)
<figcaption>Facets on the left, results in the middle, map beside them.</figcaption>
</figure>

</div>

Three regions: a **facet rail** for narrowing, the **results** themselves, and the **map**.

The layer list is off by default here — the facet rail is the browse surface, and a second list of
layers beside it would be two answers to the same question. You can switch it back on; it appears
floating over the map rather than taking a column, because the horizontal space on a catalog page
belongs to the results.

---

## What a result shows

Each dataset card carries what someone deciding whether to use it needs:

- Its **name and abstract**
- What it **is** — vector or raster, geometry type, feature count, CRS
- Its **licence and attribution**
- **How to get it** — the open-standard endpoints for that layer, ready to paste into QGIS, Python
  or R

Clicking a card adds the layer to the map; the zoom control on it flies to that dataset's extent.
So a reader can go from "what is in here?" to "show me" without leaving the page.

---

## Searching and filtering

- **Search** runs across names, descriptions and keywords.
- **Facets** narrow by folder, by type (vector, raster, external) and by licence.

Both act on the result list, and the map follows whatever the reader switches on.

---

## Layout choices

| Option | What it does |
| --- | --- |
| **Datasets listed** | *This portal's layers*, or **every public layer on the instance** |
| **Map side** | Right, bottom, or no map at all |
| **Map width** | How much of the content area the map takes (default half) |
| **Facet rail width** | How much the rail takes (default a fifth) |
| **Results per page** | How many cards before paging (default 12) |

!!! info "Portal or instance-wide"
    *This portal's layers* is a curated catalog: you choose what appears. **Every public layer**
    turns the portal into a front door for the whole instance — anything marked public shows up
    without being added to the portal. The second is what you want for an organisation's open-data
    page; the first for a project's own catalogue.

    Only layers marked public are ever listed instance-wide. A private layer is not exposed by
    switching this on.

### No map at all

Setting the map side to *none* gives a pure dataset index — a list, its facets, and the access links
for each entry. Reasonable when the datasets are national or global and a map preview adds nothing.

---

## On phones

The page stacks vertically: filters on top, results below them, map beneath. Nothing is dropped, and
the reader can still switch a dataset on and see it.

---

## Catalogs and the machine-readable index

A catalog portal is the human-facing view. The same layers are simultaneously readable by machines
through the instance's open-standard endpoints — see [Access from other tools](data-access.md).

The two stay in step because they are the same data, not two exports of it: mark a layer public and
it appears in both.

---

## When to choose something else

- One map is the point, and the layer list is enough → [Web maps](web-maps.md)
- The datasets tell a story in order → [Story maps](story-maps.md)
- Readers want summaries and filters rather than the datasets themselves → [Dashboards](dashboards.md)
