import { Badge } from "./ui";

/** VIP badge, account id and ETS client, plus any other tags as small chips. */
export function TripTags({ tags, accountId }: { tags: Record<string, unknown>; accountId: string | null | undefined }) {
  const vip = tags.vip === true || tags.vip === "true";
  const ets = typeof tags.ets_client === "string" ? tags.ets_client : null;
  const known = new Set(["vip", "ets_client", "series_id", "account_id"]);
  const other = Object.entries(tags).filter(([k, v]) => !known.has(k) && v !== null && v !== false && v !== "");
  return (
    <div className="flex flex-wrap gap-0.5">
      {vip && <Badge tone="red">VIP</Badge>}
      {accountId && (
        <Badge tone="blue" title="Account">
          acct {accountId}
        </Badge>
      )}
      {ets && (
        <Badge tone="green" title={typeof tags.series_id === "string" ? `ETS series ${tags.series_id}` : "ETS client"}>
          ets {ets}
        </Badge>
      )}
      {other.map(([k, v]) => (
        <Badge key={k}>{v === true ? k : `${k}:${typeof v === "object" ? JSON.stringify(v) : String(v)}`}</Badge>
      ))}
    </div>
  );
}
