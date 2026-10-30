import "./index.css";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { retireLegacyWorker } from "./lib/legacyWorker";

async function startApp() {
  if (await retireLegacyWorker()) return;
  const rootEl = document.getElementById("root");
  if (rootEl) {
    ReactDOM.createRoot(rootEl).render(<App />);
  }
}

void startApp();
