/** The site's places on social networks (`GET /socials`, the operator's in Admin → Settings): at the foot of the
 * landing page and under its Ask, and in Help and More in the app (owner, 2026-11-18: "so users can join and share
 * their thoughts as well as their requests to the devs"). A link only opens it, in a tab of its own: nothing of the
 * network's is loaded here, so nothing of it follows anybody around the site. */
import { useEffect, useState } from "react";

import { api, type Schema } from "./api";

type Network = { name: string; join: string; colour: string; mark: string };
/** A network the site is on: its link, and what the site draws for it. */
export type Social = Schema<"SocialLink"> & Network;

/** What the site draws for each network `libs.settings.SOCIAL_NETWORKS` names: a network without a line here is
 * left out. A mark is Simple Icons' (CC0), drawn in the network's own colour. */
export const NETWORKS: Record<string, Network> = {
  discord: {
    name: "Discord",
    join: "Join our Discord",
    colour: "#5865F2",
    mark: "M20.317 4.3698a19.7913 19.7913 0 00-4.8851-1.5152.0741.0741 0 00-.0785.0371c-.211.3753-.4447.8648-.6083 1.2495-1.8447-.2762-3.68-.2762-5.4868 0-.1636-.3933-.4058-.8742-.6177-1.2495a.077.077 0 00-.0785-.037 19.7363 19.7363 0 00-4.8852 1.515.0699.0699 0 00-.0321.0277C.5334 9.0458-.319 13.5799.0992 18.0578a.0824.0824 0 00.0312.0561c2.0528 1.5076 4.0413 2.4228 5.9929 3.0294a.0777.0777 0 00.0842-.0276c.4616-.6304.8731-1.2952 1.226-1.9942a.076.076 0 00-.0416-.1057c-.6528-.2476-1.2743-.5495-1.8722-.8923a.077.077 0 01-.0076-.1277c.1258-.0943.2517-.1923.3718-.2914a.0743.0743 0 01.0776-.0105c3.9278 1.7933 8.18 1.7933 12.0614 0a.0739.0739 0 01.0785.0095c.1202.099.246.1981.3728.2924a.077.077 0 01-.0066.1276 12.2986 12.2986 0 01-1.873.8914.0766.0766 0 00-.0407.1067c.3604.698.7719 1.3628 1.225 1.9932a.076.076 0 00.0842.0286c1.961-.6067 3.9495-1.5219 6.0023-3.0294a.077.077 0 00.0313-.0552c.5004-5.177-.8382-9.6739-3.5485-13.6604a.061.061 0 00-.0312-.0286zM8.02 15.3312c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9555-2.4189 2.157-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.9555 2.4189-2.1569 2.4189zm7.9748 0c-1.1825 0-2.1569-1.0857-2.1569-2.419 0-1.3332.9554-2.4189 2.1569-2.4189 1.2108 0 2.1757 1.0952 2.1568 2.419 0 1.3332-.946 2.4189-2.1568 2.4189Z",
  },
};

let asked: Promise<Schema<"SocialLink">[]> | null = null;

/** The networks with a link, asked once a visit whoever asks first. Not answered, there are none to show, and the
 * next page to ask asks again. */
export function useSocials(): Social[] {
  const [links, setLinks] = useState<Social[]>([]);
  useEffect(() => {
    asked ??= api.get<Schema<"SocialLink">[]>("/socials").catch(() => {
      asked = null;
      return [];
    });
    let mounted = true;
    void asked.then((all) => {
      if (!mounted) return;
      setLinks(all.flatMap((link) => {
        const network = NETWORKS[link.network];
        return network ? [{ ...network, ...link }] : [];
      }));
    });
    return () => {
      mounted = false;
    };
  }, []);
  return links;
}

/** A network's mark, in its colour. */
export function SocialMark({ social: { mark, colour } }: { social: Network }) {
  return (
    <svg className="social-mark" viewBox="0 0 24 24" style={{ color: colour }} aria-hidden="true"><path d={mark} /></svg>
  );
}
