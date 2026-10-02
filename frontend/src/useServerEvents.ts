import { useEffect, type Dispatch } from "react";
import { api, type ServerEvent } from "./api";
import type { Action } from "./state";

/** One EventSource on /api/events; reconnects (with a state refetch) when it closes. */
export function useServerEvents(dispatch: Dispatch<Action>) {
  useEffect(() => {
    let es: EventSource | null = null;
    let timer: number | undefined;
    let stopped = false;

    const connect = () => {
      es = new EventSource("/api/events");
      es.onopen = () => dispatch({ type: "connected", value: true });
      es.onmessage = (m: MessageEvent<string>) => {
        try {
          dispatch({ type: "server", event: JSON.parse(m.data) as ServerEvent });
        } catch {
          /* ignore malformed message */
        }
      };
      es.onerror = () => {
        dispatch({ type: "connected", value: false });
        if (es && es.readyState === EventSource.CLOSED && !stopped) {
          es.close();
          timer = window.setTimeout(() => {
            api.state().then((state) => dispatch({ type: "state", state }), () => undefined);
            connect();
          }, 2000);
        }
      };
    };

    api.state().then((state) => dispatch({ type: "state", state }), () => undefined);
    connect();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      es?.close();
    };
  }, [dispatch]);
}
