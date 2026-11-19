/** Discord in the app (owner, 2026-11-19: "a solid way to make sure people do not miss the fact that there is a
 * discord on the app ... link their discord first through the app, and then they can join the group after, so we
 * can assign proper roles for them"). Two steps, in order: link, on Discord's own page (`discord_routes.py`), so
 * RoleLogic, the bot in the server, gives the account their plan's role; then join. A Discord button sits in the
 * bar up top on every page with the steps behind it, and Settings and Help hold them too. */
import { useState } from "react";

import { api, type Schema } from "./api";
import { useKept } from "./kept";
import { SocialMark, useSocials } from "./socials";
import { toast } from "./toast";
import { Error, Sheet, useAction } from "./ui";

type Link = Schema<"DiscordLink">;

/** What Settings, Help and the bar's sheet read, as `kept.ts` keeps it. */
export const DISCORD = "/discord";

/** What each way back from Discord's page means (`discord_routes._back`). */
const SAID: Record<string, string> = {
  linked: "Your Discord is linked. Join the server, and your plan's role is there.",
  cancelled: "Your Discord wasn't linked.",
  failed: "Your Discord didn't link. Try again in a minute.",
};

/** Back from Discord's page: how it went, said once, and taken off the address so a reload says nothing. */
export function sayBackFromDiscord(): void {
  const said = new URLSearchParams(window.location.search).get("discord");
  if (!said) return;
  window.history.replaceState(null, "", window.location.pathname + window.location.hash);
  if (SAID[said]) toast({ text: SAID[said] });
}

/** Why join, in a line, for the places that show the steps. */
export const WHY_DISCORD = "Get help with your applications, talk with other people looking for work, and tell "
  + "the people who build Mahoraga what you want next.";

/** Link, then join: the next one to do is the main button. Where linking is not set up here, joining alone. */
export function DiscordSteps() {
  const { data: link, reload } = useKept<Link>(DISCORD);
  const discord = useSocials().find((social) => social.network === "discord");
  const start = useAction(async () => {
    window.location.assign((await api.post<Schema<"LinkPage">>(`${DISCORD}/link`)).url);
  });
  const unlink = useAction(async () => {
    await api.del(DISCORD);
    await reload();
  });
  if (!link || !discord) return null;
  const linked = link.name !== null;
  const join = (
    <a className={`button ${linked || !link.available ? "main" : "secondary"}`} href={discord.url}
       target="_blank" rel="noreferrer">
      {discord.join}
    </a>
  );
  if (!link.available) return join;
  return (
    <>
      <ol className="steps">
        <li className={linked ? "done" : undefined}>
          <span className="step-number" aria-hidden="true">1</span>
          <div>
            <strong>{linked ? `Linked as ${link.name}` : "Link your Discord"}</strong>
            {linked ? (
              <p className="small muted">
                Your plan's role is waiting for you in the server.{" "}
                <button type="button" className="linklike small" disabled={unlink.busy}
                        onClick={() => void unlink.run()}>Unlink</button>
              </p>
            ) : (
              <>
                <p className="small muted">So the server knows your plan and gives you its role.</p>
                <button type="button" className="main" disabled={start.busy} onClick={() => void start.run()}>
                  <SocialMark social={discord} />Link my Discord
                </button>
              </>
            )}
          </div>
        </li>
        <li>
          <span className="step-number" aria-hidden="true">2</span>
          <div>
            <strong>Join the server</strong>
            <p className="small muted">Then come and say hello.</p>
            {join}
          </div>
        </li>
      </ol>
      <Error>{start.error ?? unlink.error}</Error>
    </>
  );
}

/** Discord, up top on every page: a picture anybody knows, and the steps behind it. */
export function DiscordButton() {
  const [open, setOpen] = useState(false);
  const discord = useSocials().find((social) => social.network === "discord");
  if (!discord) return null;
  return (
    <>
      <button type="button" className="bell discord-button" aria-label="Discord" title="Join our Discord"
              onClick={() => setOpen(true)}>
        <SocialMark social={discord} />
      </button>
      {open ? (
        <Sheet title="Join us on Discord" onClose={() => setOpen(false)}>
          <p className="discord-why">{WHY_DISCORD}</p>
          <DiscordSteps />
        </Sheet>
      ) : null}
    </>
  );
}
