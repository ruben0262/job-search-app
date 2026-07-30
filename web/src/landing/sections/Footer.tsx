import { GITHUB_URL } from "../sections";
import { GitHubIcon } from "../components/Icons";

export default function Footer() {
  return (
    <footer className="footer">
      <div className="foot-name">Mahoraga</div>
      <div className="foot-links">
        <a href={GITHUB_URL} target="_blank" rel="noreferrer">
          <GitHubIcon size={14} className="inline-icon" /> GitHub
        </a>
        <a href="https://rubenlekli.com" target="_blank" rel="noreferrer">
          rubenlekli.com
        </a>
      </div>
      <div className="foot-note">
        Self-hosted, open source · the wheel keeps turning
      </div>
    </footer>
  );
}
