---
description: >-
  Remove GeoDeploy from a server — cleanly, and without taking anything else with it. What each
  option deletes, what survives, and how to keep your data first.
---

# Uninstalling

GeoDeploy installs into one directory and a set of Docker containers, so removing it is a small,
contained operation. **Nothing outside that directory and those containers is touched** — including
anything else the machine was already serving.

Read the first section before running anything: the difference between the options is what happens
to your data, and one of them is not reversible.

## Which one do you want?

| | What it does | Your data |
| --- | --- | --- |
| **Stop it** | Containers stop; everything stays on disk | Untouched |
| **Remove the software** | Containers, images and network removed; the directory stays | Untouched, on disk |
| **Remove everything** | The above, plus the install directory | **Deleted** |

## Stop it, keep everything

The gentlest option, and enough if you only want the ports back or the memory freed:

```bash
cd ~/geodeploy
sudo docker compose down
```

Everything is still there — layers, portals, users, settings. `sudo docker compose up -d` brings it
back exactly as it was. This is what you want before, say, giving port 80 to something else, or
while you move the server.

## Remove the software, keep the data

```bash
cd ~/geodeploy
sudo docker compose down --remove-orphans
sudo docker rmi geodeploy/api:latest geodeploy/ui:latest
sudo docker network rm geodeploy
```

The install directory stays, and with it `data/` (your PostGIS files, object storage, published
portals) and `.env` (your settings and secret key). Reinstalling over the top of it picks up exactly
where you left off.

## Remove everything

!!! danger "This deletes your data, and there is no undo"
    `reset.sh` removes the install directory, and that directory contains **the database, the object
    storage and every published portal**. If you might want any of it, take a backup first — see
    below — and check the backup is somewhere else.

```bash
cd ~/geodeploy
sudo bash installer/reset.sh
cd ~
```

It asks for confirmation (`yes`, typed in full) and then removes, in order:

- every container whose name contains `geodeploy`
- the `geodeploy/api` and `geodeploy/ui` images
- the `geodeploy` Docker network
- any Docker volumes named `geodeploy*`
- the install directory itself, `~/geodeploy`

The `cd ~` afterwards is not optional housekeeping: the script deletes the directory you are standing
in, and your shell's next command otherwise fails with `getcwd: cannot access parent directories`.

### Back up first, and check where the backup is

!!! warning "Local object storage is inside the directory being deleted"
    If GeoDeploy provisioned MinIO for you, **every bucket lives under `~/geodeploy/data/minio`** —
    including any backups you told GeoDeploy to write there. `reset.sh` deletes the lot. A backup
    only survives this if it is on **another server or another provider**.

Take one from **Settings → Backups**, confirm it landed at its destination, and confirm that
destination is not this machine. [Backups and restore](backups.md) covers restoring it onto a new
server afterwards.

Two things worth copying out separately, because they are small and easy to forget:

```bash
cp ~/geodeploy/.env ~/geodeploy-env-backup      # then move it OFF this machine
```

`.env` holds `GEODEPLOY_SECRET_KEY`, and that key is what your SMTP password, OIDC client secret and
storage secret are encrypted with. Restoring a backup **without** it recovers everything except those
three, which have to be re-entered. It is deliberately not in any backup, so that a stolen backup
cannot hand over your credentials.

## If you connected your own database or storage

GeoDeploy never touches infrastructure it did not create. An external PostGIS or S3 bucket is left
completely alone by every option above — including `reset.sh`, which only removes containers it
recognises and the local install directory.

That also means **your data is still there** after uninstalling, and still costs whatever it costs.
Removing it is yours to do, in your provider's console or with `DROP DATABASE`, once you are sure
nothing else needs it.

## Things GeoDeploy did not install

Worth stating plainly, because the whole point of the shared-machine mode is that other things live
here too:

- **Docker itself** is left installed. `install.sh` may have installed it for you; uninstalling
  GeoDeploy does not remove it, because anything else on the machine may now depend on it.
- **Your reverse proxy configuration.** This is the one thing GeoDeploy may have written *outside*
  its own directory, so it is the one to deal with first.

    **If GeoDeploy configured it for you** (Settings → Deployment → Apply), remove it from there —
    **before you uninstall**, while the dashboard still exists. **Remove it** deletes the file it
    wrote, tests the configuration, and only then reloads. That ordering matters: taking the file
    away can expose an unrelated problem elsewhere in your configuration, and a tidy-up must not be
    what takes the other sites on this machine down.

    **If you have already removed GeoDeploy**, or you placed the file yourself, do it by hand. The
    file is `geodeploy.conf` and carries a `managed by GeoDeploy` comment — check that before
    deleting, in case the name collides with something of yours:

    ```bash
    grep -l 'managed by GeoDeploy' /etc/nginx/conf.d/geodeploy.conf       /etc/nginx/sites-available/geodeploy.conf 2>/dev/null

    sudo rm -f /etc/nginx/conf.d/geodeploy.conf
    sudo rm -f /etc/nginx/sites-enabled/geodeploy.conf /etc/nginx/sites-available/geodeploy.conf
    sudo nginx -t && sudo systemctl reload nginx     # test FIRST; reload only if it passes
    ```

    Caddy: `/etc/caddy/conf.d/geodeploy.caddy`, then `sudo systemctl reload caddy`. Apache:
    `/etc/apache2/sites-available/geodeploy.conf` plus its `sites-enabled` symlink, then
    `sudo apache2ctl configtest && sudo systemctl reload apache2`.

    Left in place, the file is harmless but points at a port nothing answers on — visitors get a 502.

- **Your DNS record is left alone.** The A record you created still points at this server. Remove it
  in the same control panel you created it in, or repoint it, otherwise the name resolves to a
  machine that no longer serves it.
- **Certificates** issued by certbot for that name remain until you run
  `sudo certbot delete --cert-name your-domain`.

## Reinstalling afterwards

```bash
curl -fsSL https://raw.githubusercontent.com/bravemaster3/geodeploy/main/installer/install.sh | bash
```

A fresh install asks the port question again from scratch. To land on the same port as before without
being asked, pass it: `bash -s -- --port 8081`.
