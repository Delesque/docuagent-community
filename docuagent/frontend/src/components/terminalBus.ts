export interface TerminalHandle {
  write: (text: string) => void;
  writeln: (text: string) => void;
  clear: () => void;
}

let handle: TerminalHandle | null = null;

export function setTerminalHandle(next: TerminalHandle | null): void {
  handle = next;
}

export function writeToTerminal(text: string): void {
  handle?.write(text);
}

export function writelnToTerminal(text: string): void {
  handle?.writeln(text);
}

export function clearTerminal(): void {
  handle?.clear();
}
