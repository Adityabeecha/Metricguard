import { Component, type ErrorInfo, type ReactNode } from 'react';

type Props = { children: ReactNode; onHome: () => void; resetKey: string };
type State = { error: Error | null };

/** Keeps a crash in one page from blanking the whole app; resets when the route changes. */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('MetricGuard page error', error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (prev.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: null });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="gate-screen" role="alert">
        <div className="gate-card">
          <h1>This page could not be displayed</h1>
          <p>Something on this page failed to load. Your saved work is not affected. Go back to the investigations list or try again.</p>
          <div className="gate-actions">
            <button className="button primary" onClick={this.props.onHome}>Go to investigations</button>
            <button className="button secondary" onClick={() => this.setState({ error: null })}>Try again</button>
          </div>
          <details className="gate-help"><summary>Technical detail</summary><p className="mono-cell">{this.state.error.message}</p></details>
        </div>
      </div>
    );
  }
}
