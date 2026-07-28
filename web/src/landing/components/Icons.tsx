interface IconProps {
  size?: number;
  className?: string;
}

/** Telegram paper plane, monochrome. */
export function TelegramIcon({ size = 18, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M21.94 4.6 18.9 18.95c-.23 1.01-.83 1.26-1.68.78l-4.64-3.42-2.24 2.15c-.25.25-.46.46-.94.46l.33-4.72 8.6-7.77c.37-.33-.08-.52-.58-.19L7.12 12.93l-4.57-1.43c-.99-.31-1.01-.99.21-1.47L20.5 3.4c.83-.31 1.55.19 1.44 1.2z" />
    </svg>
  );
}

/** GitHub mark, monochrome. */
export function GitHubIcon({ size = 18, className }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="currentColor"
      className={className}
      aria-hidden="true"
    >
      <path d="M12 .3a12 12 0 0 0-3.8 23.4c.6.1.8-.3.8-.6v-2c-3.3.7-4-1.6-4-1.6-.6-1.4-1.4-1.8-1.4-1.8-1.1-.7.1-.7.1-.7 1.2.1 1.9 1.2 1.9 1.2 1.1 1.8 2.8 1.3 3.5 1 .1-.8.4-1.3.8-1.6-2.7-.3-5.5-1.3-5.5-5.9 0-1.3.5-2.4 1.2-3.2-.1-.3-.5-1.5.1-3.2 0 0 1-.3 3.3 1.2a11.5 11.5 0 0 1 6 0c2.3-1.5 3.3-1.2 3.3-1.2.6 1.7.2 2.9.1 3.2.7.8 1.2 1.9 1.2 3.2 0 4.6-2.8 5.6-5.5 5.9.4.4.8 1.1.8 2.2v3.3c0 .3.2.7.8.6A12 12 0 0 0 12 .3" />
    </svg>
  );
}

/** Claude spark: a 12-ray burst, drawn in-house, monochrome. */
export function ClaudeIcon({ size = 18, className }: IconProps) {
  const rays = Array.from({ length: 12 }, (_, i) => (360 / 12) * i);
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      aria-hidden="true"
    >
      {rays.map((a) => (
        <line
          key={a}
          x1="12"
          y1="7.6"
          x2="12"
          y2="2.6"
          stroke="currentColor"
          strokeWidth="2.1"
          strokeLinecap="round"
          transform={`rotate(${a} 12 12)`}
        />
      ))}
    </svg>
  );
}
