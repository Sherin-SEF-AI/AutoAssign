import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { isYmd, tomorrowIST } from "../lib/time";

/** Selected service date, kept in the `date` URL search param. Defaults to tomorrow in IST. */
export function useServiceDate(): [string, (d: string) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("date");
  const date = isYmd(raw) ? raw : tomorrowIST();
  const setDate = useCallback(
    (d: string) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (isYmd(d)) next.set("date", d);
          else next.delete("date");
          // A plan version belongs to one date: drop the selection when the date changes.
          if (prev.get("date") !== next.get("date")) {
            next.delete("plan");
            next.delete("against");
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );
  return [date, setDate];
}

/** Read/write a single search param without touching the others. */
export function useSearchParam(name: string): [string | null, (v: string | null) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(name);
  const set = useCallback(
    (v: string | null) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (v === null || v === "") next.delete(name);
          else next.set(name, v);
          return next;
        },
        { replace: true },
      );
    },
    [name, setParams],
  );
  return [value, set];
}
