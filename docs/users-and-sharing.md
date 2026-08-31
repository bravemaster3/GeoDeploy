---
description: >-
  Roles from viewer to owner, per-layer visibility, invitation links, scoped API tokens and an audit log — how access works in a self-hosted GeoDeploy instance.
---

# Users, roles and sharing

GeoDeploy is a **shared workspace**: everyone signed in sees the same catalog of layers and portals,
and their role decides what they may change. On top of that, each layer carries its own visibility,
so individual datasets can be narrowed or opened up.

## Roles

| Role | Can do |
| --- | --- |
| **Viewer** | See layers and portals. Change nothing. |
| **Editor** | Upload, style, edit and publish. The working role for most people. |
| **Admin** | Everything an editor can, plus manage users and see the activity log. |
| **Owner** | Everything, plus instance settings, backups and restore. Exactly one owner. |

Ownership is transferable, but there is always exactly one owner — the account that cannot be locked
out of its own server.

## Inviting people

**Settings ▸ Users ▸ Invite** creates a single-use link and the role the new account will get.

You can send it two ways:

- **Copy the link** and pass it on however you like — chat, your own mail, anything. No mail server
  needed, which is the default and works out of the box.
- **Let GeoDeploy email it**, by configuring an SMTP service under **Settings ▸ Email** — Resend, or
  any SMTP provider. Invitations and password resets then arrive by mail without you forwarding
  anything.

If someone leaves, deleting their account reassigns what they created to the owner, so nothing is
orphaned or silently deleted.

## Layer visibility

Every layer has one of three visibility levels, set from the layer's row in **My Data**:

| Visibility | Who can see it in the workspace | Reachable without signing in |
| --- | --- | --- |
| **Private** | Its creator, plus admins and the owner | No |
| **Organization** *(default)* | Everyone signed in | No |
| **Public** | Everyone signed in | **Yes** — appears in the catalog and its data is downloadable |

!!! warning "Public means publicly readable"
    Setting a layer to *Public* puts it in the open catalog and makes its underlying data readable
    over the internet without a login — that is the point of it, and it is how QGIS and other tools
    consume your data. Use *Organization* for anything you do not want anyone to fetch.

A layer can still be drawn on a published portal without being *Public*. Portal access and layer
visibility are separate: the portal decides who sees the map, the layer decides who can pull the
data on its own.

!!! note "External sources"
    Sources that point at somebody else's server (XYZ, WMS, WFS) offer only *Private* and
    *Organization*. There is no data held here to publish, so a public tier would not do anything.
    They still render on public portals like any other layer.

## API tokens

For scripts, notebooks and plugins, create a token under **Settings ▸ API tokens**. Tokens:

- are shown **once** at creation — copy it then; only a hash is stored
- carry the scopes you grant (read data, write data, publish portals, and so on)
- never exceed their owner's current role, so demoting someone weakens their tokens too
- can be revoked at any time, and stop working when their owner's account is removed

See the [API reference](api-reference.md) for how to use one.

## Activity log

Administrators get an append-only record of who did what — uploads, edits, publishes, role changes,
sign-ins, token creation. It is filterable by user, action and time range, and entries survive the
deletion of the user who caused them.

## Sessions and sign-in

- Passwords can be changed by the user and reset by an administrator.
- Changing a password ends that account's other sessions.
- **Single sign-on (OpenID Connect)** can be configured under **Settings ▸ Authentication** if your
  organisation already has an identity provider.

    !!! warning "Not yet verified against a live provider"
        The SSO path is implemented but has not been tested end-to-end against a real identity
        provider. Treat it as unproven, and keep a password account you can still sign in with.
