# Assumptions

Every assumption made while building blurabbit-dispatch, grouped by area. Each one is a
default that can be changed through configuration or the data contract unless noted.

## Data and synthetic world

- A1. **ETS trips are not account restricted.** ETS runs carry `tags.ets_client` (four synthetic
  ETS clients) and `tags.series_id`. `account_id` is reserved for the corporate accounts A1 to A5
  whose trips may only be served by that account's three approved drivers.
- A2. **ETS series run every day of the week**, so volume stays near 120 trips per day. About 46
  base series (half morning, half evening) with each series kept on a given day with probability
  0.9 (the 10% churn).
- A3. **Driver weekly off rotates by ISO week**: driver `i` is off when `(i + iso_week) mod 7`
  equals the weekday. This yields 25 or 26 drivers on duty every day.
- A4. **Uber shifts**: on half of the days one on-duty driver has `channel_today = uber` and is
  excluded from planning. The spec does not give a rate.
- A5. **Hub AC point count**: each hub has 6 AC 7.2 kW points (the spec gives the power only) and
  2 DC 30 kW points.
- A6. **Vehicle classes**: Windsor and ZS EV are `suv`; Tigor EV and eC3 are `sedan`. Trips with
  `vehicle_class = any` can use either.
- A7. **`DataSource.list_vehicles()` has no date argument**, so it returns the fleet's current
  status. For the synthetic source this is the status on today's IST date (the 10% service days
  are evaluated for today). Seeded historical snapshots use each day's own status.
- A8. **SOC check-ins** are reported 20 minutes before shift start, only for on-duty drivers. The
  nightly solve always assumes 100% (overnight charging policy) and ignores check-ins; the 06:00
  validation reads them.
- A9. **Lifecycle timestamps** for synthetic trips: booked (6 h to 7 days ahead), assigned (21:30
  IST the evening before), en_route, arrived, started, completed; cancelled trips carry
  `cancelled`; no-shows carry `arrived` and `no_show` (15 minutes after the scheduled pickup).
  Future days contain only booked and cancelled trips.
- A10. **Package trips** have a contractual duration of `package_hours`; their pickup equals the
  drop and their actual distance is drawn between 30 and 110 km. They are excluded from ETA
  residual logging.
- A11. **Addresses** are synthetic (`<n> Main Road, <zone>, Bengaluru`) and are the only address
  data shown to drivers.

## Snapshots and ingest

- A12. **Snapshots are deduplicated by content hash.** Ingesting identical content for a date
  reuses the latest snapshot id, so jobs that compare snapshot ids stay idempotent.
- A13. **Trip changes and trip lifecycle are stored separately.** `trip.created`, `trip.updated`
  and `trip.cancelled` events create a new snapshot (parent linked) because they change what the
  planner sees. `trip.status` events (en route, arrived, started, completed, with actuals) update
  `trip_live`, which overlays the snapshot in read models.
- A14. **Ops fleet edits** (vehicle status, driver shift exceptions) are stored as
  `fleet_exception` rows and applied on top of DataSource records at ingest, so the next ingest
  does not silently revert them.
- A15. **Pings are partitioned by day** with a DEFAULT partition; daily partitions on IST day
  boundaries are created ahead of time by the worker.

## Infrastructure

- A16. **OSRM extract**: Geofabrik does not publish a Karnataka-only extract, so `make osrm-data`
  downloads the India southern zone extract, which contains Karnataka.
- A17. **Web served behind nginx** on port 5173 in compose, proxying `/api` to the api service,
  so the browser talks to one origin.
- A18. **Login rate limit** is per client IP per calendar minute (5 attempts).
- A19. **Runtime settings** edited through `PUT /settings` are stored as overrides in the
  database and merged over the environment values. Only buffers, caps, thresholds and provider
  toggles are editable; infrastructure settings stay environment only.
