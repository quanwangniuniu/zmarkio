import axios from "axios";
import type { PublicKPIShare } from "@/types/report";

// No Authorization header. The shared `api` client would attach a leftover
// JWT and turn an anonymous share-page read into a 401.
const publicApi = axios.create({
  baseURL: process.env.NEXT_PUBLIC_API_URL || "",
  timeout: 10000,
  headers: {
    "Content-Type": "application/json",
    Accept: "application/json, text/plain, */*",
  },
});

/** Read one project's Custom KPIs by share token. 410 means the link expired. */
export const getPublicKPIShare = (token: string) =>
  publicApi.get<PublicKPIShare>(
    `/api/report/share/${encodeURIComponent(token)}/`
  );
