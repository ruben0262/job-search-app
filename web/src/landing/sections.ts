export interface Section {
  id: string;
  label: string;
}

// Order defines both page order and wheel spoke order.
export const SECTIONS: Section[] = [
  { id: "top", label: "Mahoraga" },
  { id: "what", label: "What it is" },
  { id: "how", label: "How it works" },
  { id: "live", label: "Your jobs" },
  { id: "start", label: "Get started" },
];

