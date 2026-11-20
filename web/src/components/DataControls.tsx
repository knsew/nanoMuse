import { Download, Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { useStore } from "../store";
import type { SyncState } from "../types";
import { cx } from "../util";
import { MuseCaption, MuseCard, MuseDivider, MuseRow, MuseSwitchRow } from "./MuseList";

/** The policy the apps link when the relay has not said where its own is. */
export const PRIVACY_URL = "https://github.com/knsew/nanoMuse";

/**
 * Settings → Data controls: syncing conversations between the account's devices, and
 * *Download your agent data* (a zip of what this runtime keeps). There is no "help
 * improve" switch: this deployment keeps no training samples.
 */
export function DataControls({ flush = false }: { /** the parent already pads 16px: pull the cards out to its edge */ flush?: boolean }) {
  const t = useT();
  const { state, toast } = useStore();
  const signedIn = !!state.hub?.account.signed_in;
  const [exporting, setExporting] = useState(false);

  /** *Download your agent data* (parity note 37): the runtime answers with the zip. */
  const download = async () => {
    setExporting(true);
    try {
      const { blob, name } = await api.exportData();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = name;
      link.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast((e as Error).message);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className={cx(flush && "-mx-4")}>
      <SyncControls signedIn={signedIn} />
      <MuseCard className="mt-3">
        <MuseRow icon={exporting ? <Loader2 size={22} className="animate-spin" /> : <Download size={22} />} label={t("Download your agent data")} onClick={() => void download()} chevron={false} />
        <MuseDivider inset={16} />
        <MuseCaption className="px-4 pb-2.5 pt-2">{t("A zip of what this runtime keeps about you: the memory, the goals, the reminders, the triggers, the address book and the chats. It never contains the keys themselves.")}</MuseCaption>
      </MuseCard>
    </div>
  );
}

/**
 * The first card of Data controls (contract C7): *Sync conversations between my devices*, on
 * by default while signed in. Off tells the relay, which deletes what it stores; on pushes
 * this device's chats again. *Delete synced conversations* empties the relay's store and
 * keeps the switch and the chats on every device.
 */
export function SyncControls({ signedIn }: { signedIn: boolean }) {
  const t = useT();
  const { toast } = useStore();
  const [sync, setSync] = useState<SyncState | null>(null);
  const [busy, setBusy] = useState(false);
  const counts = sync?.relay?.counts ?? { conversations: 0, messages: 0 };
  const stored = signedIn && (counts.conversations > 0 || counts.messages > 0);

  const load = async () => {
    try {
      setSync(await api.syncState());
    } catch {
      /* an older runtime without sync: the card shows the switch as unavailable */
    }
  };
  useEffect(() => {
    void load();
  }, [signedIn]);

  const run = async (what: () => Promise<SyncState>, said: string) => {
    setBusy(true);
    try {
      setSync(await what());
      toast(said);
    } catch (e) {
      toast((e as Error).message);
      await load();
    } finally {
      setBusy(false);
    }
  };
  const flip = (on: boolean) => {
    if (!on && stored && !window.confirm(t("Off deletes the synced conversations from nanoMuse Cloud. The chats on each device stay."))) return;
    void run(
      () => api.syncSetState(on),
      on ? t("On. Your devices show the same conversations from now on.") : t("Off. Nothing is kept on nanoMuse Cloud any more."),
    );
  };
  const wipe = () => {
    if (!window.confirm(t("The synced conversations are removed from nanoMuse Cloud. The chats on each device stay. This cannot be undone."))) return;
    void run(() => api.syncDelete(), t("Synced conversations deleted."));
  };
  // C9: side chats, per device — off by default; on brings the other devices' over once
  const flipSide = (on: boolean) => {
    void run(
      () => api.syncSetSideChats(on),
      on ? t("On. This device's side chats go to the account; the other devices' are on their way.") : t("Off. Side chats stay on this device from now on."),
    );
  };
  const syncOn = signedIn && !!sync?.enabled;

  return (
    <div className="mb-6">
      <MuseCard>
        <MuseSwitchRow label={t("Sync conversations between my devices")} checked={syncOn} disabled={busy || !signedIn || !sync} onChange={flip} />
      </MuseCard>
      <MuseCaption>
        {t("The text of your chats is kept on nanoMuse Cloud so every device shows the same conversations. Files and images stay on the device they were made on.")}{" "}
        {!signedIn ? t("Sign in to nanoMuse Cloud to use it.") : sync?.paused ? t("Paused: sign in again to continue.") : stored && t("{c} chats, {m} messages kept so far.", { c: String(counts.conversations), m: String(counts.messages) })}
      </MuseCaption>
      {sync && "side_chats" in sync && (
        <>
          <MuseCard className="mt-3">
            <MuseSwitchRow label={t("Also sync side chats")} checked={syncOn && !!sync.side_chats} disabled={busy || !syncOn} onChange={flipSide} />
          </MuseCard>
          <MuseCaption>{t("Off: side chats stay on this device. On: this device's side chats go to the account and the other devices' side chats come here.")}</MuseCaption>
        </>
      )}
      {stored && (
        <MuseCard className="mt-3">
          <MuseRow icon={busy ? <Loader2 size={22} className="animate-spin" /> : <Trash2 size={22} />} label={t("Delete synced conversations")} value={t("{n} chats", { n: String(counts.conversations) })} onClick={wipe} chevron={false} />
          <MuseDivider inset={16} />
          <MuseCaption className="px-4 pb-2.5 pt-2">{t("Removes what nanoMuse Cloud stores for this account; the chats on each device stay, and the switch stays on. Deleting a chat on one device deletes it on all of them.")}</MuseCaption>
        </MuseCard>
      )}
    </div>
  );
}
