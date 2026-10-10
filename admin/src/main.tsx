import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router-dom';
import App from './App.tsx';
import { FeedbackProvider } from './Feedback';
// Self-hosted fonts (same pair as the public site); no third-party requests.
import '@fontsource/instrument-serif/latin-400.css';
import '@fontsource-variable/manrope/wght.css';
import './index.css';

// HashRouter: the UI is a directory of static files served next to the API
// (ADR-029, same origin). With routes in the fragment, the static server never
// needs a fallback for deep links such as #/piezas/<id>.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <FeedbackProvider>
        <App />
      </FeedbackProvider>
    </HashRouter>
  </StrictMode>
);
