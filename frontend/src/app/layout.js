import "./globals.css";
// import 'highlight.js/styles/atom-one-dark.min.css';
import { AppProviders } from '../components/providers/AppProviders';
import { StoreProvider } from '../store/StoreProvider';

export const metadata = {
  title: "Marketing Simplified - Campaign Management",
  description: "Professional advertising campaign management platform",
  icons: {
    icon: "/icon.svg",
  },
};

export default function RootLayout({ children }) {
  return (
    <html lang="en">
      <head>
      </head>
      <body>
        <StoreProvider>
          <AppProviders>{children}</AppProviders>
        </StoreProvider>
      </body>
    </html>
  );
}
