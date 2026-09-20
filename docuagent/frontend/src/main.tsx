import { Component, StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App.v2";
import "./index.css";

/** Catch render crashes so a black screen never happens silently. */
class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <div className="flex h-screen flex-col items-center justify-center gap-4 bg-paper px-8 text-center">
          <p className="font-display text-[17px] text-chalk">出错了，请刷新页面重试</p>
          <pre className="max-w-xl overflow-auto rounded-lg border border-ink-ghost bg-paper-raise p-4 font-mono text-[10px] text-chalk-dim text-left">
            {this.state.error.message}
          </pre>
        </div>
      );
    }
    return this.props.children;
  }
}

const container = document.getElementById("root");
if (!container) throw new Error("缺少 #root 挂载点。");

createRoot(container).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
);
