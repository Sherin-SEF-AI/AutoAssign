import { Placeholder } from "../components/ui";
import { useAuth } from "../state/auth";

export default function SettingsPage() {
  const { isAdmin } = useAuth();
  return (
    <div className="space-y-2">
      <Placeholder title="Settings: solver weights, providers and budgets" />
      {!isAdmin && <div className="text-center text-xs text-neutral-500">Settings are read-only for the ops role.</div>}
    </div>
  );
}
