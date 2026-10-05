import { useState, type FormEvent } from "react";
import { useServiceDate } from "../state/date";
import { useDrivers, useHubs, useSocCheckin, useVehicles, type Driver } from "../state/queries";
import { ApiError } from "../api/errors";
import { fmtDateTime, fmtTime } from "../lib/time";
import { ErrorBanner, toast } from "../components/Toast";
import { Badge, EmptyRow, Section, Spinner, StatusChip, TableWrap, Td, Th, btnPrimaryCls, fmtNum, inputCls } from "../components/ui";

export default function FleetPage() {
  const [date] = useServiceDate();
  return (
    <div className="space-y-5">
      <DriversTable date={date} />
      <VehiclesTable date={date} />
      <HubsTable />
    </div>
  );
}

function SocBar({ pct }: { pct: number | null | undefined }) {
  if (pct === null || pct === undefined) return <span className="text-neutral-400">-</span>;
  const color = pct < 20 ? "bg-red-500" : pct < 40 ? "bg-amber-500" : "bg-emerald-500";
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-block h-1.5 w-10 overflow-hidden rounded bg-neutral-200">
        <span className={`block h-full ${color}`} style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
      </span>
      <span className="font-mono">{fmtNum(pct, 0)}%</span>
    </span>
  );
}

function DriversTable({ date }: { date: string }) {
  const q = useDrivers(date);
  const rows = q.data ?? [];
  return (
    <Section
      title={
        <>
          Drivers <span className="font-normal text-neutral-500">{date} ({rows.length})</span>
        </>
      }
      right={q.isFetching && <Spinner label="Refreshing" />}
    >
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap className="max-h-[60vh]">
        <thead>
          <tr>
            <Th>Name</Th>
            <Th>Shift (IST)</Th>
            <Th>Active</Th>
            <Th>Skills</Th>
            <Th>Vehicle</Th>
            <Th>Veh. status</Th>
            <Th>Channel</Th>
            <Th>SOC</Th>
            <Th className="text-right">Odometer km</Th>
            <Th className="text-right">Trips</Th>
            <Th>SOC check-in</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={11}><Spinner /></EmptyRow>}
          {!q.isLoading && rows.length === 0 && <EmptyRow cols={11}>No drivers rostered for this date</EmptyRow>}
          {rows.map((d) => (
            <tr key={d.driver_id} className={d.active ? "" : "text-neutral-400"}>
              <Td className="whitespace-nowrap">{d.name}</Td>
              <Td className="whitespace-nowrap font-mono">
                {fmtTime(d.shift_start_at)} to {fmtTime(d.shift_end_at)}
              </Td>
              <Td>{d.active ? <Badge tone="green">yes</Badge> : <Badge>no</Badge>}</Td>
              <Td>
                <div className="flex flex-wrap gap-0.5">
                  {d.skills.map((s) => (
                    <Badge key={s}>{s}</Badge>
                  ))}
                  {d.approved_accounts.length > 0 && <Badge tone="blue" title={d.approved_accounts.join(", ")}>{d.approved_accounts.length} accts</Badge>}
                </div>
              </Td>
              <Td className="whitespace-nowrap">
                {d.vehicle_model ?? "-"}
                {d.vehicle_registration && <div className="font-mono text-[11px] text-neutral-500">{d.vehicle_registration}</div>}
              </Td>
              <Td>
                <StatusChip status={d.vehicle_status} />
              </Td>
              <Td>{d.channel_today}</Td>
              <Td className="whitespace-nowrap">
                <SocBar pct={d.soc_pct} />
                {d.soc_reported_at && <div className="text-[10px] text-neutral-500">{fmtDateTime(d.soc_reported_at)}</div>}
              </Td>
              <Td className="text-right font-mono">{fmtNum(d.odometer_km, 0)}</Td>
              <Td className="text-right font-mono">{d.assigned_trips ?? 0}</Td>
              <Td>
                <SocCheckinForm driver={d} date={date} />
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </Section>
  );
}

function SocCheckinForm({ driver, date }: { driver: Driver; date: string }) {
  const m = useSocCheckin();
  const [soc, setSoc] = useState("");
  const [odo, setOdo] = useState("");
  const [localErr, setLocalErr] = useState<string | null>(null);
  const vehicleId = driver.vehicle_id;
  const apiErr = m.error instanceof ApiError ? m.error : null;
  const socErr = apiErr?.fieldError("soc_pct");
  const odoErr = apiErr?.fieldError("odometer_km");

  if (!vehicleId) return <span className="text-neutral-400">no vehicle</span>;

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setLocalErr(null);
    if (soc.trim() === "") {
      setLocalErr("SOC % is required");
      return;
    }
    const socN = Number(soc);
    const odoN = odo.trim() === "" ? null : Number(odo);
    if (!Number.isFinite(socN) || (odoN !== null && !Number.isFinite(odoN))) {
      setLocalErr("Enter numbers only");
      return;
    }
    // Range checks are left to the API so its validation errors are shown verbatim.
    m.mutate(
      { vehicleId, body: { soc_pct: socN, odometer_km: odoN, service_date: date, driver_id: driver.driver_id } },
      {
        onSuccess: (out) => {
          toast.success(`SOC saved for ${driver.vehicle_registration ?? driver.name}`, `${out.soc_pct}%${out.odometer_km != null ? `, ${out.odometer_km} km` : ""}`);
          setSoc("");
          setOdo("");
        },
      },
    );
  };

  return (
    <form onSubmit={submit} noValidate className="min-w-[220px]">
      <div className="flex items-center gap-1">
        <input
          aria-label="SOC percent"
          inputMode="decimal"
          placeholder="SOC %"
          className={`${inputCls} w-14 ${socErr ? "border-red-400" : ""}`}
          value={soc}
          onChange={(e) => setSoc(e.target.value)}
        />
        <input
          aria-label="Odometer km"
          inputMode="decimal"
          placeholder="Odo km"
          className={`${inputCls} w-20 ${odoErr ? "border-red-400" : ""}`}
          value={odo}
          onChange={(e) => setOdo(e.target.value)}
        />
        <button type="submit" className={btnPrimaryCls} disabled={m.isPending}>
          {m.isPending ? "..." : "Save"}
        </button>
      </div>
      {localErr && <div className="mt-0.5 text-[11px] text-red-700">{localErr}</div>}
      {apiErr && (
        <div className="mt-0.5 text-[11px] text-red-700">
          <span className="font-mono">{apiErr.code}</span>: {socErr ?? odoErr ?? apiErr.detail}
          {apiErr.errors
            .filter((f) => !f.field.endsWith("soc_pct") && !f.field.endsWith("odometer_km"))
            .map((f, i) => (
              <div key={i}>
                {f.field}: {f.message}
              </div>
            ))}
        </div>
      )}
    </form>
  );
}

function VehiclesTable({ date }: { date: string }) {
  const q = useVehicles(date);
  const rows = q.data ?? [];
  return (
    <Section
      title={
        <>
          Vehicles <span className="font-normal text-neutral-500">({rows.length})</span>
        </>
      }
      right={q.isFetching && <Spinner label="Refreshing" />}
    >
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap className="max-h-[60vh]">
        <thead>
          <tr>
            <Th>Registration</Th>
            <Th>Model / variant</Th>
            <Th>Class</Th>
            <Th className="text-right">Battery kWh</Th>
            <Th className="text-right">Plan cap km</Th>
            <Th className="text-right">Wh/km</Th>
            <Th className="text-right">Seats</Th>
            <Th>Luggage</Th>
            <Th>Status</Th>
            <Th>Hub</Th>
            <Th>Driver</Th>
            <Th>SOC</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={12}><Spinner /></EmptyRow>}
          {!q.isLoading && rows.length === 0 && <EmptyRow cols={12}>No vehicles</EmptyRow>}
          {rows.map((v) => (
            <tr key={v.vehicle_id}>
              <Td className="whitespace-nowrap font-mono">{v.registration}</Td>
              <Td className="whitespace-nowrap">
                {v.model} <span className="text-neutral-500">{v.variant}</span>
              </Td>
              <Td>{v.vehicle_class}</Td>
              <Td className="text-right font-mono">{fmtNum(v.battery_kwh, 1)}</Td>
              <Td className="text-right font-mono">{fmtNum(v.planning_cap_km, 0)}</Td>
              <Td className="text-right font-mono">{fmtNum(v.e_roll_wh_per_km, 0)}</Td>
              <Td className="text-right font-mono">{v.seats}</Td>
              <Td>{v.luggage_class}</Td>
              <Td>
                <StatusChip status={v.status} />
              </Td>
              <Td className="whitespace-nowrap">{v.hub_name ?? "-"}</Td>
              <Td className="whitespace-nowrap">{v.driver_name ?? "-"}</Td>
              <Td>
                <SocBar pct={v.soc_pct} />
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </Section>
  );
}

function HubsTable() {
  const q = useHubs();
  const rows = q.data ?? [];
  return (
    <Section title="Hubs">
      {q.error && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <TableWrap className="max-w-3xl">
        <thead>
          <tr>
            <Th>Name</Th>
            <Th>Location</Th>
            <Th className="text-right">AC points x kW</Th>
            <Th className="text-right">DC points x kW</Th>
          </tr>
        </thead>
        <tbody>
          {q.isLoading && <EmptyRow cols={4}><Spinner /></EmptyRow>}
          {!q.isLoading && rows.length === 0 && <EmptyRow cols={4}>No hubs</EmptyRow>}
          {rows.map((h) => (
            <tr key={h.hub_id}>
              <Td>{h.name}</Td>
              <Td className="font-mono">
                {h.lat.toFixed(4)}, {h.lng.toFixed(4)}
              </Td>
              <Td className="text-right font-mono">
                {h.ac_points} x {fmtNum(h.ac_kw, 1)}
              </Td>
              <Td className="text-right font-mono">
                {h.dc_points} x {fmtNum(h.dc_kw, 1)}
              </Td>
            </tr>
          ))}
        </tbody>
      </TableWrap>
    </Section>
  );
}
