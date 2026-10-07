'use client';

import { useEffect, useState } from 'react';
import toast from 'react-hot-toast';
import { ExternalLink, Loader2, Monitor, Smartphone, X } from 'lucide-react';

import {
  facebookApi,
  type MetaAdPreviewFormat,
  type MetaCreativePreview,
} from '@/lib/api/facebookApi';

const FORMAT_OPTIONS: {
  key: MetaAdPreviewFormat;
  label: string;
  icon: React.ReactNode;
}[] = [
  { key: 'MOBILE_FEED_STANDARD', label: 'Mobile Feed', icon: <Smartphone className="h-3.5 w-3.5" /> },
  { key: 'DESKTOP_FEED_STANDARD', label: 'Desktop Feed', icon: <Monitor className="h-3.5 w-3.5" /> },
  { key: 'INSTAGRAM_STANDARD', label: 'Instagram', icon: <Smartphone className="h-3.5 w-3.5" /> },
  { key: 'FACEBOOK_STORY_MOBILE', label: 'FB Story', icon: <Smartphone className="h-3.5 w-3.5" /> },
  { key: 'INSTAGRAM_STORY', label: 'IG Story', icon: <Smartphone className="h-3.5 w-3.5" /> },
  { key: 'FACEBOOK_REELS_MOBILE', label: 'FB Reels', icon: <Smartphone className="h-3.5 w-3.5" /> },
];

export default function VideoModal({
  creativeId,
  open,
  onClose,
  title,
}: {
  creativeId: number | string | null;
  open: boolean;
  onClose: () => void;
  title?: string;
}) {
  const [format, setFormat] = useState<MetaAdPreviewFormat>('MOBILE_FEED_STANDARD');
  const [preview, setPreview] = useState<MetaCreativePreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    // Drop cached iframe payload whenever the modal is closed (or has no
    // creative) so reopening never flashes another account's preview.
    if (!open || !creativeId) {
      setPreview(null);
      setErrorMsg(null);
      setLoading(false);
      return;
    }
    let active = true;
    setLoading(true);
    setErrorMsg(null);
    setPreview(null);
    facebookApi
      .getMetaCreativePreview(creativeId, format)
      .then((d) => {
        if (!active) return;
        setPreview(d);
      })
      .catch((err) => {
        if (!active) return;
        const detail = err?.response?.data?.detail || 'Failed to load preview.';
        setErrorMsg(detail);
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [open, creativeId, format]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [open, onClose]);

  if (!open || !creativeId) return null;

  const isStoryFormat =
    format === 'FACEBOOK_STORY_MOBILE' ||
    format === 'INSTAGRAM_STORY' ||
    format === 'FACEBOOK_REELS_MOBILE';
  const iframeWidth = isStoryFormat ? 360 : 540;
  const iframeHeight = isStoryFormat ? 640 : 700;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="relative flex max-h-[90vh] w-full max-w-[980px] flex-col overflow-hidden rounded-2xl bg-white shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-start justify-between gap-4 border-b border-gray-100 px-5 py-3">
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-wide text-[#1a9ba3]">
              Creative preview
            </div>
            <h2 className="mt-0.5 truncate text-sm font-semibold text-gray-900">
              {title || preview?.ad_name || 'Meta Ad Preview'}
            </h2>
            {preview?.meta_ad_id && (
              <div className="mt-0.5 text-[11px] text-gray-400">
                Rendered from ad{' '}
                <span className="font-mono">{preview.meta_ad_id}</span>
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1.5 text-gray-500 transition-colors hover:bg-gray-100 hover:text-gray-900"
            aria-label="Close preview"
          >
            <X className="h-4 w-4" />
          </button>
        </header>

        <div className="flex flex-wrap items-center gap-1.5 border-b border-gray-100 bg-gray-50/60 px-5 py-2">
          {FORMAT_OPTIONS.map((opt) => (
            <button
              key={opt.key}
              onClick={() => setFormat(opt.key)}
              className={`inline-flex items-center gap-1 rounded-md border px-2.5 py-1 text-[11px] font-medium transition-colors ${
                format === opt.key
                  ? 'border-[#3CCED7] bg-white text-[#1a9ba3]'
                  : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50'
              }`}
            >
              {opt.icon}
              {opt.label}
            </button>
          ))}
        </div>

        <div className="flex flex-1 items-center justify-center overflow-y-auto bg-[#f5f6f7] p-6">
          {loading && !preview ? (
            <div className="flex h-60 w-60 flex-col items-center justify-center gap-2 text-gray-400">
              <Loader2 className="h-6 w-6 animate-spin" />
              <span className="text-xs">Asking Meta for the preview…</span>
            </div>
          ) : errorMsg ? (
            <div className="flex max-w-md flex-col items-center gap-3 rounded-lg border border-red-200 bg-red-50 p-4 text-center">
              <div className="text-sm font-medium text-red-700">
                Preview unavailable
              </div>
              <p className="text-xs text-red-600">{errorMsg}</p>
              <a
                href={`https://business.facebook.com/adsmanager/manage/ads`}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 rounded-md border border-red-200 bg-white px-2 py-1 text-[11px] text-red-700 hover:bg-red-100"
              >
                Open Ads Manager <ExternalLink className="h-3 w-3" />
              </a>
            </div>
          ) : preview?.iframe_src ? (
            <div
              className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm"
              style={{ width: iframeWidth, height: iframeHeight }}
            >
              <iframe
                title={`Meta preview for ${preview.meta_ad_id}`}
                src={preview.iframe_src}
                width={iframeWidth}
                height={iframeHeight}
                frameBorder={0}
                allow="autoplay; encrypted-media; picture-in-picture"
              />
            </div>
          ) : (
            <div className="text-sm text-gray-500">No preview data available.</div>
          )}
        </div>

        <footer className="flex items-center justify-between gap-3 border-t border-gray-100 bg-white px-5 py-2.5 text-[11px] text-gray-500">
          <span>
            Meta renders this iframe server-side with video autoplay. ESC to close.
          </span>
          {preview?.meta_ad_id && (
            <a
              href={`https://business.facebook.com/adsmanager/manage/ads?selected_ad_ids=${preview.meta_ad_id}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-[11px] text-gray-700 hover:bg-gray-50"
            >
              Open in Ads Manager <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </footer>
      </div>
    </div>
  );
}
