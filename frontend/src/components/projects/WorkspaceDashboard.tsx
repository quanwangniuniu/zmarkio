'use client';

// SMP-472: Project Workspace Dashboard
// Charts rendered with pure CSS/SVG — no Chart.js dependency.
// Data:
//   Zone metrics  → ProjectWorkspaceDashboardView  (/api/dashboard/workspace/)
//   Chart metrics → DashboardSummaryView           (/api/dashboard/summary/)

import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import Link from 'next/link';
import { Id } from '@/types/common';
import {
  WorkspaceAPI,
  type DashboardSummaryData,
  type WorkspaceDashboardData,
  type WorkspaceSpreadsheet,
} from '@/lib/api/workspaceApi';
import { nestedProjectPath } from '@/lib/projectNestedRoutes';
import { DashboardTileIcon, useDashboardTileControls } from '@/components/dashboard/builder/DashboardTileControls';

// ── Constants ────────────────────────────────────────────────────────────────

const ACTIVE_SHEET_DAYS = 7;
const OVERVIEW_PRIMARY = '#3CCED7';
const SURFACE_BORDER = '#e5e7eb';
const SURFACE_RADIUS = 12;
const PANEL_PAD = '14px 16px';
const TEXT_MUTED = '#9ca3af';
const TEXT_SECONDARY = '#6b7280';
const TEXT_PRIMARY = '#334155';

const COLOR = {
  green: '#34d399',
  blue: OVERVIEW_PRIMARY,
  orange: '#fbbf24',
  red: '#fb7185',
  gray: '#d1d5db',
  purple: '#a78bfa',
  slate: '#94a3b8',
  teal: '#2dd4bf',
} as const;

const PRIORITY_CONFIG = [
  { key: 'HIGHEST', label: 'Highest', color: COLOR.red },
  { key: 'HIGH', label: 'High', color: COLOR.orange },
  { key: 'MEDIUM', label: 'Medium', color: COLOR.blue },
  { key: 'LOW', label: 'Low', color: COLOR.slate },
  { key: 'LOWEST', label: 'Lowest', color: COLOR.gray },
];

const TYPE_COLORS = [COLOR.blue, COLOR.green, COLOR.orange, COLOR.purple, COLOR.slate];

const STATUS_COLOR: Record<string, string> = {
  Done: COLOR.green,
  'In Progress': COLOR.blue,
  'To Do': '#cbd5e1',
  Research: COLOR.purple,
  Cancelled: COLOR.slate,
};

const STATUS_EXPLANATION: Record<string, string> = {
  'To Do': 'To Do: includes Draft and Submitted tasks waiting to be worked on.',
  'In Progress': 'In Progress: includes tasks currently under review.',
  Done: 'Done: includes Approved and Locked tasks.',
  Research: 'Research: includes Rejected tasks that need rework or further analysis.',
  Cancelled: 'Cancelled: includes tasks that were intentionally stopped.',
};

const RISK_COLOR: Record<string, string> = {
  HIGH: COLOR.red,
  MEDIUM: COLOR.orange,
  LOW: COLOR.green,
};

// ── Utility helpers ──────────────────────────────────────────────────────────

function daysAgo(dateStr: string): number {
  const d = new Date(dateStr);
  if (Number.isNaN(d.getTime())) return 999;
  return Math.floor((Date.now() - d.getTime()) / 86_400_000);
}

function pct(numerator: number, denominator: number): number {
  if (denominator === 0) return 0;
  return Math.round((numerator / denominator) * 100);
}

function relTime(dateStr: string | null): string {
  if (!dateStr) return '—';
  const days = daysAgo(dateStr);
  if (days === 0) return 'today';
  if (days === 1) return '1d ago';
  return `${days}d ago`;
}

// ── Tiny shared components ────────────────────────────────────────────────────

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ fontSize: 10, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: TEXT_MUTED, marginBottom: 8 }}>
      {children}
    </p>
  );
}

function Divider() {
  return <div style={{ height: 1, background: '#f2f3f5', margin: '10px 0' }} />;
}

function MetricRow({ label, value, valueColor = TEXT_PRIMARY, tooltip }: {
  label: string; value: string | number; valueColor?: string;
  /** Optional tooltip shown on hover — gives extra context without navigating away. */
  tooltip?: string;
}) {
  const [hovered, setHovered] = useState(false);
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '7px 0', borderBottom: '1px solid #f2f3f5',
        position: 'relative',
        transition: 'background 0.12s',
        gap: 8,
        minWidth: 0,
      }}
    >
      <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12, color: TEXT_SECONDARY }}>{label}</span>
      <span style={{ minWidth: 0, maxWidth: '55%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', textAlign: 'right', fontSize: 13, fontWeight: 600, color: valueColor, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </span>
      {/* Tooltip — shown on hover when tooltip prop is provided */}
      {tooltip && hovered && (
        <div style={{
          position: 'absolute', right: 0, bottom: 'calc(100% + 4px)',
          background: '#475569', color: '#fff',
          fontSize: 11, padding: '5px 9px', borderRadius: 6,
          whiteSpace: 'nowrap', zIndex: 50, pointerEvents: 'none',
          boxShadow: '0 2px 8px rgba(0,0,0,0.18)',
        }}>
          {tooltip}
          <div style={{
            position: 'absolute', top: '100%', right: 16,
            borderWidth: '4px', borderStyle: 'solid',
            borderColor: '#475569 transparent transparent transparent',
          }} />
        </div>
      )}
    </div>
  );
}

function SegBar({ segments }: { segments: { flex: number; color: string; title: string }[] }) {
  const total = segments.reduce((s, seg) => s + seg.flex, 0);
  if (total === 0) return null;
  return (
    <div style={{ display: 'flex', gap: 2, height: 5, borderRadius: 3, overflow: 'hidden' }}>
      {segments.map((seg, i) => (
        <span key={i} title={seg.title} style={{ display: 'block', flex: seg.flex, background: seg.color }} />
      ))}
    </div>
  );
}

function LegendItem({ color, label, tooltip }: { color: string; label: string; tooltip?: string }) {
  const [hovered, setHovered] = useState(false);
  return (
    <span
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{ display: 'inline-flex', alignItems: 'center', gap: 3, fontSize: 9, color: '#888', position: 'relative' }}
    >
      <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0, display: 'inline-block' }} />
      {label}
      {tooltip && hovered && (
        <span
          style={{
            position: 'absolute',
            left: 0,
            bottom: 'calc(100% + 6px)',
            background: '#111827',
            color: '#fff',
            fontSize: 11,
            padding: '5px 9px',
            borderRadius: 6,
            whiteSpace: 'nowrap',
            zIndex: 50,
            pointerEvents: 'none',
            boxShadow: '0 2px 8px rgba(0,0,0,0.22)',
          }}
        >
          {tooltip}
          <span
            style={{
              position: 'absolute',
              top: '100%',
              left: 14,
              borderWidth: '4px',
              borderStyle: 'solid',
              borderColor: '#111827 transparent transparent transparent',
            }}
          />
        </span>
      )}
    </span>
  );
}

// ── useCountUp: animates a number from 0 to target on mount ─────────────────

function useCountUp(target: number, duration = 600): number {
  const [current, setCurrent] = useState(0);
  useEffect(() => {
    if (target === 0) { setCurrent(0); return; }
    let startTime: number | null = null;
    const start = 0;
    function step(ts: number) {
      if (!startTime) startTime = ts;
      const progress = Math.min((ts - startTime) / duration, 1);
      // easeOutCubic
      const eased = 1 - Math.pow(1 - progress, 3);
      setCurrent(Math.round(start + (target - start) * eased));
      if (progress < 1) requestAnimationFrame(step);
    }
    const raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [target, duration]);
  return current;
}

// ── Summary card with count-up animation + click-to-expand panel ─────────────

function SummaryCard({
  widgetId, label, rawValue, displayValue, valueColor = TEXT_PRIMARY,
  sub, subColor = '#aaa', barFill, barColor = COLOR.green,
}: {
  widgetId: WorkspaceWidgetId;
  label: string;
  rawValue: number;
  displayValue?: string;
  valueColor?: string;
  sub?: string; subColor?: string;
  barFill?: number; barColor?: string;
}) {
  const tileControls = useDashboardTileControls(widgetId);
  const [hovered, setHovered] = useState(false);
  const animated = useCountUp(rawValue);

  const suffix = displayValue?.replace(String(rawValue), '') ?? '';
  const shownValue = `${animated}${suffix}`;

  return (
    <div style={{ position: 'relative', minWidth: 0, height: '100%' }}>
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        style={{
          background: '#fff',
          borderRadius: SURFACE_RADIUS,
          padding: PANEL_PAD,
          border: `1px solid ${hovered ? '#d1d5db' : SURFACE_BORDER}`,
          boxShadow: 'none',
          transform: 'translateY(0)',
          transition: 'all 0.18s ease',
          position: 'relative',
          minWidth: 0,
          overflow: 'hidden',
          height: '100%',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', height: 28, position: 'relative', marginBottom: 8, minWidth: 0 }}>
          {tileControls?.dragHandle && <span className="absolute left-0 top-0 z-10">{tileControls.dragHandle}</span>}
          <span className={tileControls ? 'group-hover:pl-8 group-focus-within:pl-8' : ''} style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 10, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase', color: TEXT_MUTED }}>{label}</span>
          {tileControls?.removeButton && <span style={{ marginLeft: 'auto' }}>{tileControls.removeButton}</span>}
        </div>
        <div>
          <div style={{ fontSize: 32, fontWeight: 700, color: valueColor, lineHeight: 1, letterSpacing: 0 }}>
            {shownValue}
          </div>
          {barFill !== undefined && (
            <div style={{ height: 3, background: '#eef0f2', borderRadius: 2, marginTop: 10, overflow: 'hidden' }}>
              <div style={{
                height: 3, borderRadius: 2, background: barColor,
                width: `${(animated / Math.max(rawValue, 1)) * barFill}%`,
                transition: 'width 0.05s linear',
              }} />
            </div>
          )}
          {sub && <div style={{ fontSize: 10, color: subColor, marginTop: 5, overflowWrap: 'anywhere' }}>{sub}</div>}
        </div>
      </div>
    </div>
  );
}

// ── Zone panel ───────────────────────────────────────────────────────────────

function ZonePanel({ widgetId, iconSvg, iconBg, title, badge, viewAllHref, children }: {
  widgetId: WorkspaceWidgetId;
  iconSvg: React.ReactNode; iconBg: string; title: string;
  badge: string; viewAllHref: string; children: React.ReactNode;
}) {
  const tileControls = useDashboardTileControls(widgetId);
  const [hovered, setHovered] = useState(false);
  const viewAll = <Link href={viewAllHref} style={{ fontSize: 12, color: OVERVIEW_PRIMARY, whiteSpace: 'nowrap', flexShrink: 0, textDecoration: 'none', fontWeight: 500 }}>View all</Link>;
  return (
    <div
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        background: '#fff', borderRadius: SURFACE_RADIUS, padding: PANEL_PAD,
        border: `1px solid ${hovered ? '#d1d5db' : SURFACE_BORDER}`,
        boxShadow: 'none',
        transition: 'all 0.18s ease',
        minWidth: 0,
        overflow: 'hidden',
        height: '100%',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, gap: 8, minWidth: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: '1 1 0', minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <DashboardTileIcon dragHandle={tileControls?.dragHandle} icon={<div style={{ width: 28, height: 28, borderRadius: 7, background: iconBg, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              {iconSvg}
            </div>} />
            <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', fontSize: 13, fontWeight: 600, color: TEXT_PRIMARY, whiteSpace: 'nowrap' }}>{title}</span>
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexShrink: 0 }}>
          {badge ? (
            <span style={{ fontSize: 11, color: TEXT_SECONDARY, background: '#f8fafc', borderRadius: 9999, padding: '2px 8px', whiteSpace: 'nowrap' }}>
              {badge}
            </span>
          ) : null}
          {viewAll}
          {tileControls?.removeButton}
        </div>
      </div>
      {children}
    </div>
  );
}

// ── Chart panel ──────────────────────────────────────────────────────────────

function ChartPanel({ widgetId, section, title, children, right, full = false }: {
  widgetId: WorkspaceWidgetId;
  section: string; title: string; children: React.ReactNode; right?: React.ReactNode; full?: boolean;
}) {
  const tileControls = useDashboardTileControls(widgetId);
  return (
    <div style={{ background: '#fff', borderRadius: SURFACE_RADIUS, border: `1px solid ${SURFACE_BORDER}`, padding: PANEL_PAD, gridColumn: full ? '1 / -1' : undefined, boxShadow: 'none', minWidth: 0, overflow: 'hidden', height: '100%' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', height: 28, marginBottom: 4, minWidth: 0 }}>
        <DashboardTileIcon icon={<span style={{ fontSize: 9, color: TEXT_MUTED, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{section}</span>} dragHandle={tileControls?.dragHandle} />
        {tileControls?.removeButton}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14, gap: 8, minWidth: 0 }}>
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 12, fontWeight: 600, color: TEXT_PRIMARY, marginRight: 'auto' }}>{title}</span>
        {right}
      </div>
      {children}
    </div>
  );
}

// ── Pure SVG charts (interactive) ────────────────────────────────────────────

/**
 * Horizontal bar chart with hover tooltip.
 * Hovering a bar dims all others and shows a floating label with count + %.
 */
function HorizontalBarChart({ labels, values, colors }: {
  labels: string[]; values: number[]; colors: string[];
}) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const total = values.reduce((s, v) => s + v, 0);
  const max = Math.max(...values, 1);
  const ROW_H = 28;
  const LABEL_W = 80;
  const BAR_AREA = 300;
  const height = labels.length * ROW_H + 8;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${LABEL_W + BAR_AREA + 60} ${height}`}
      style={{ overflow: 'visible', cursor: 'default' }}
    >
      {labels.map((label, i) => {
        const barW = Math.max(4, Math.round((values[i] / max) * BAR_AREA));
        const y = i * ROW_H + ROW_H / 2;
        const active = hoveredIdx === i;
        const dimmed = hoveredIdx !== null && !active;
        const percentage = pct(values[i], total);

        return (
          <g
            key={label}
            onMouseEnter={() => setHoveredIdx(i)}
            onMouseLeave={() => setHoveredIdx(null)}
            style={{ cursor: 'pointer' }}
          >
            {/* invisible hit area for easier hover */}
            <rect x={LABEL_W} y={y - 14} width={BAR_AREA} height={28} fill="transparent" />
            <text
              x={LABEL_W - 8}
              y={y}
              textAnchor="end"
              dominantBaseline="middle"
              fontSize={6}
              fill={dimmed ? '#ccc' : '#555'}
              style={{ transition: 'fill 0.15s' }}
            >
              {label}
            </text>
            <rect
              x={LABEL_W} y={y - 10} width={barW} height={20} rx={4}
              fill={colors[i]}
              opacity={dimmed ? 0.3 : active ? 1 : 0.85}
              style={{ transition: 'opacity 0.15s' }}
            />
            {/* count label */}
            <text
              x={LABEL_W + barW + 6}
              y={y}
              dominantBaseline="middle"
              fontSize={11}
              fill={dimmed ? '#ddd' : '#aaa'}
              style={{ transition: 'fill 0.15s' }}
            >
              {values[i]}
            </text>
            {/* tooltip on active bar */}
            {active && (
              <g>
                <rect
                  x={LABEL_W + barW + 28} y={y - 16}
                  width={68} height={20} rx={4}
                  fill="#111827"
                />
                <text
                  x={LABEL_W + barW + 62} y={y - 2}
                  textAnchor="middle" fontSize={10} fill="#fff" fontWeight={500}
                >
                  {percentage}% of total
                </text>
              </g>
            )}
          </g>
        );
      })}
    </svg>
  );
}

/**
 * Donut chart with hover interaction:
 * - Hovered segment expands outward slightly (scale transform from centre)
 * - Centre shows hovered label + count; default shows total
 */
function DonutChart({ labels, values, colors }: {
  labels: string[]; values: number[]; colors: string[];
}) {
  const [hoveredIdx, setHoveredIdx] = useState<number | null>(null);
  const total = values.reduce((s, v) => s + v, 0);

  // Empty state
  if (total === 0 || labels.length === 0) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 14, padding: '8px 0', minWidth: 0 }}>
        <svg width={80} height={80} style={{ flexShrink: 0 }}>
          <circle cx={40} cy={40} r={30} fill="none" stroke="#f0f0f0" strokeWidth={12} />
          <circle cx={40} cy={40} r={16} fill="#f7f8fa" />
        </svg>
        <div>
          <p style={{ fontSize: 12, color: '#bbb', margin: 0 }}>No task type data</p>
          <p style={{ fontSize: 11, color: '#d0d0d0', margin: '3px 0 0' }}>Tasks will appear here once created</p>
        </div>
      </div>
    );
  }

  const CX = 64, CY = 64, R = 54, IR = 36;

  // Single segment — full circle
  if (values.filter(v => v > 0).length === 1) {
    const activeIdx = values.findIndex(v => v > 0);
    const isHovered = hoveredIdx === activeIdx;
    return (
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, minWidth: 0 }}>
        <svg width={128} height={128} style={{ flexShrink: 0 }}>
          <circle
            cx={CX} cy={CY} r={isHovered ? R + 4 : R}
            fill={colors[activeIdx] ?? COLOR.blue}
            style={{ transition: 'r 0.18s' }}
            onMouseEnter={() => setHoveredIdx(activeIdx)}
            onMouseLeave={() => setHoveredIdx(null)}
          />
          <circle cx={CX} cy={CY} r={IR} fill="#fff" />
          <text x={CX} y={CY - 4} textAnchor="middle" fontSize={11} fontWeight={600} fill={TEXT_PRIMARY}>
            {labels[activeIdx]}
          </text>
          <text x={CX} y={CY + 10} textAnchor="middle" fontSize={10} fill="#888">100%</text>
        </svg>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 10, color: '#555' }}>
            <span style={{ width: 8, height: 8, borderRadius: 2, background: colors[activeIdx] ?? COLOR.blue, flexShrink: 0, display: 'inline-block' }} />
            {labels[activeIdx]} (100%)
          </div>
        </div>
      </div>
    );
  }

  // Build segments
  let angle = -Math.PI / 2;
  const segments = values.map((v, i) => {
    if (v === 0) return null;
    const sweep = (v / total) * 2 * Math.PI;
    // Normal radius path
    const makeArc = (outerR: number, innerR: number) => {
      const x1 = CX + outerR * Math.cos(angle);
      const y1 = CY + outerR * Math.sin(angle);
      const x2 = CX + outerR * Math.cos(angle + sweep);
      const y2 = CY + outerR * Math.sin(angle + sweep);
      const ix1 = CX + innerR * Math.cos(angle + sweep);
      const iy1 = CY + innerR * Math.sin(angle + sweep);
      const ix2 = CX + innerR * Math.cos(angle);
      const iy2 = CY + innerR * Math.sin(angle);
      const large = sweep > Math.PI ? 1 : 0;
      return `M${x1.toFixed(2)} ${y1.toFixed(2)} A${outerR} ${outerR} 0 ${large} 1 ${x2.toFixed(2)} ${y2.toFixed(2)} L${ix1.toFixed(2)} ${iy1.toFixed(2)} A${innerR} ${innerR} 0 ${large} 0 ${ix2.toFixed(2)} ${iy2.toFixed(2)} Z`;
    };
    const seg = {
      path: makeArc(R, IR),
      pathExpanded: makeArc(R + 6, IR),
      color: colors[i], label: labels[i], value: v, index: i,
    };
    angle += sweep;
    return seg;
  }).filter(Boolean) as { path: string; pathExpanded: string; color: string; label: string; value: number; index: number }[];

  const hov = hoveredIdx !== null ? segments.find(s => s.index === hoveredIdx) : null;

  return (
      <div style={{ display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: 12, minWidth: 0 }}>
      <svg width={128} height={128} style={{ flexShrink: 0, cursor: 'pointer' }}>
        {segments.map((seg) => {
          const isHov = hoveredIdx === seg.index;
          return (
            <path
              key={seg.index}
              d={isHov ? seg.pathExpanded : seg.path}
              fill={seg.color}
              stroke="#fff"
              strokeWidth={1.5}
              opacity={hoveredIdx !== null && !isHov ? 0.45 : 1}
              style={{ transition: 'opacity 0.15s' }}
              onMouseEnter={() => setHoveredIdx(seg.index)}
              onMouseLeave={() => setHoveredIdx(null)}
            />
          );
        })}
        {/* Centre label */}
        {hov ? (
          <>
            <text x={CX} y={CY - 5} textAnchor="middle" fontSize={10} fontWeight={600} fill={TEXT_PRIMARY}>
              {hov.label.length > 8 ? hov.label.slice(0, 8) + '…' : hov.label}
            </text>
            <text x={CX} y={CY + 9} textAnchor="middle" fontSize={10} fill="#888">
              {hov.value} · {pct(hov.value, total)}%
            </text>
          </>
        ) : (
          <>
            <text x={CX} y={CY - 5} textAnchor="middle" fontSize={10} fontWeight={600} fill="#aaa">Total</text>
            <text x={CX} y={CY + 9} textAnchor="middle" fontSize={13} fontWeight={700} fill={TEXT_PRIMARY}>{total}</text>
          </>
        )}
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
        {segments.map((seg) => (
          <div
            key={seg.index}
            onMouseEnter={() => setHoveredIdx(seg.index)}
            onMouseLeave={() => setHoveredIdx(null)}
            style={{
              display: 'flex', alignItems: 'center', gap: 5,
              fontSize: 10, color: '#555', cursor: 'pointer',
              opacity: hoveredIdx !== null && hoveredIdx !== seg.index ? 0.4 : 1,
              transition: 'opacity 0.15s',
            }}
          >
            <span style={{ width: 8, height: 8, borderRadius: 2, background: seg.color, flexShrink: 0, display: 'inline-block' }} />
            {seg.label} ({pct(seg.value, total)}%)
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Line chart with crosshair + tooltip on hover.
 * Moving the mouse over the chart area snaps to the nearest data point column
 * and shows a vertical reference line with a floating tooltip.
 */
function LineChart({ labels, series }: {
  labels: string[];
  series: { label: string; values: number[]; color: string; dashed?: boolean }[];
}) {
  const [activeIdx, setActiveIdx] = useState<number | null>(null);
  const W = 480, H = 140;
  const PAD = { top: 12, right: 20, bottom: 24, left: 28 };
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;
  const allValues = series.flatMap((s) => s.values);
  const maxVal = Math.max(...allValues, 2);
  const gridVals = [0, Math.round(maxVal / 2), maxVal];
  const n = labels.length;
  const xStep = innerW / Math.max(n - 1, 1);

  function toXY(i: number, v: number) {
    return { x: PAD.left + i * xStep, y: PAD.top + innerH - (v / maxVal) * innerH };
  }

  // Convert SVG-coordinate mouseX to nearest data index
  function handleMouseMove(e: React.MouseEvent<SVGSVGElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    // Scale from DOM pixels to viewBox coordinates
    const scale = W / rect.width;
    const svgX = (e.clientX - rect.left) * scale;
    const rawIdx = (svgX - PAD.left) / xStep;
    const idx = Math.max(0, Math.min(n - 1, Math.round(rawIdx)));
    setActiveIdx(idx);
  }

  // Tooltip box dimensions
  const TIP_W = 82, TIP_H = series.length * 11 + 13;

  return (
    <svg
      width="100%"
      viewBox={`0 0 ${W} ${H}`}
      style={{ overflow: 'visible', cursor: 'crosshair' }}
      onMouseMove={handleMouseMove}
      onMouseLeave={() => setActiveIdx(null)}
    >
      {/* Grid lines */}
      {gridVals.map((gv) => {
        const y = PAD.top + innerH - (gv / maxVal) * innerH;
        return (
          <g key={gv}>
            <line x1={PAD.left} y1={y} x2={W - PAD.right} y2={y}
              stroke="rgba(0,0,0,0.05)" strokeWidth={1} />
            <text x={PAD.left - 4} y={y + 1.4} textAnchor="end" fontSize={3.5} fill="#aaa">{gv}</text>
          </g>
        );
      })}

      {/* X-axis labels */}
      {labels.map((lbl, i) => {
        if (!lbl) return null;
        const { x } = toXY(i, 0);
        return (
          <text key={i} x={x} y={H - 10} textAnchor="middle" fontSize={3.5}
            fill={activeIdx === i ? '#555' : '#aaa'}
            fontWeight={activeIdx === i ? 600 : 400}
            style={{ transition: 'fill 0.1s' }}
          >
            {lbl}
          </text>
        );
      })}

      {/* Series lines + dots */}
      {series.map((s) => {
        const points = s.values.map((v, i) => toXY(i, v));
        const polyline = points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(' ');
        return (
          <g key={s.label}>
            <polyline points={polyline} fill="none" stroke={s.color} strokeWidth={1.8}
              strokeDasharray={s.dashed ? '5 3' : undefined}
              strokeLinecap="round" strokeLinejoin="round"
            />
            {points.map((p, i) => (
              <circle
                key={i} cx={p.x} cy={p.y}
                r={activeIdx === i ? 4 : 2.5}
                fill={activeIdx === i ? '#fff' : s.color}
                stroke={s.color}
                strokeWidth={activeIdx === i ? 2 : 0}
                style={{ transition: 'r 0.1s' }}
              />
            ))}
          </g>
        );
      })}

      {/* Crosshair + tooltip */}
      {activeIdx !== null && (() => {
        const { x } = toXY(activeIdx, 0);
        // Position tooltip: flip left if too close to right edge
        const tipX = x + 8 + TIP_W > W - PAD.right ? x - TIP_W - 8 : x + 8;
        const tipY = PAD.top;

        return (
          <g pointerEvents="none">
            {/* Vertical reference line */}
            <line
              x1={x} y1={PAD.top} x2={x} y2={PAD.top + innerH}
              stroke="#c0c0c0" strokeWidth={1} strokeDasharray="3 2"
            />
            {/* Tooltip box */}
            <rect x={tipX} y={tipY} width={TIP_W} height={TIP_H} rx={4} fill="#475569" />
            {/* Date label */}
            <text x={tipX + 6} y={tipY + 8} fontSize={4.5} fill="#aaa" fontWeight={600}>
              {labels[activeIdx] || `Day ${activeIdx + 1}`}
            </text>
            {/* Series values */}
            {series.map((s, si) => (
              <g key={s.label}>
                <circle cx={tipX + 8} cy={tipY + 12.5 + si * 11} r={2} fill={s.color} />
                <text x={tipX + 13} y={tipY + 14 + si * 11} fontSize={3.5} fill="#fff">
                  {s.label}: {s.values[activeIdx] ?? 0}
                </text>
              </g>
            ))}
          </g>
        );
      })()}
    </svg>
  );
}

// ── Priority bar row ─────────────────────────────────────────────────────────

function PriorityRow({ label, count, max, color }: { label: string; count: number; max: number; color: string }) {
  const w = max === 0 ? 0 : Math.max(4, Math.round((count / max) * 100));
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 7 }}>
      <span style={{ fontSize: 11, color: '#666', width: 54, flexShrink: 0 }}>{label}</span>
      <div style={{ flex: 1, height: 5, background: '#f2f3f5', borderRadius: 3, overflow: 'hidden' }}>
        <div style={{ height: 5, borderRadius: 3, background: color, width: `${w}%` }} />
      </div>
      <span style={{ fontSize: 11, color: '#aaa', width: 18, textAlign: 'right', flexShrink: 0 }}>{count}</span>
    </div>
  );
}

// ── Operations mini bar chart ────────────────────────────────────────────────

function SheetMiniBarChart({ spreadsheets }: { spreadsheets: WorkspaceSpreadsheet[] }) {
  const CHART_H = 48;
  const MIN_H = 8;

  const active = spreadsheets
    .filter((s) => daysAgo(s.updated_at) < ACTIVE_SHEET_DAYS)
    .slice(0, 12);

  if (active.length === 0) {
    return (
      <div style={{ marginTop: 6, padding: '10px 0', display: 'flex', alignItems: 'center', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 3, height: 28, flexShrink: 0 }}>
          {[40, 60, 30, 50, 20].map((h, i) => (
            <div key={i} style={{ width: 10, height: `${h}%`, background: '#f0f0f0', borderRadius: '2px 2px 0 0' }} />
          ))}
        </div>
        <div>
          <p style={{ fontSize: 11, color: '#bbb', margin: 0, fontWeight: 500 }}>No recent activity</p>
          <p style={{ fontSize: 10, color: '#d0d0d0', margin: '2px 0 0' }}>Sheets edited in the last 7d will appear here</p>
        </div>
      </div>
    );
  }

  return (
    <div style={{ marginTop: 4 }}>
      <div style={{ overflowX: 'auto', paddingBottom: 2 }}>
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: 5, height: CHART_H, minWidth: 'max-content' }}>
          {active.map((s) => {
            const days = daysAgo(s.updated_at);
            const ratio = 1 - days / ACTIVE_SHEET_DAYS;
            const barH = Math.max(MIN_H, Math.round(ratio * CHART_H));
            const color = s.has_running_job ? COLOR.teal : '#d4d8dd';
            const ageLbl = days === 0 ? 'today' : days === 1 ? '1d ago' : `${days}d ago`;
            return (
              <div
                key={s.id}
                title={`${s.name}\nUpdated ${ageLbl}${s.has_running_job ? '\n● Running job' : ''}`}
                style={{
                  width: 12,
                  minWidth: 12,
                  height: barH,
                  background: color,
                  borderRadius: '3px 3px 0 0',
                  cursor: 'default',
                  transition: 'opacity 0.15s',
                }}
                onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.75')}
                onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
              />
            );
          })}
        </div>
      </div>
      <div style={{ display: 'flex', gap: 10, marginTop: 7 }}>
        <LegendItem color={COLOR.teal} label="Has running job" />
        <LegendItem color="#d4d8dd" label="Idle" />
      </div>
    </div>
  );
}

// ── Derived metric helpers ───────────────────────────────────────────────────

function deriveDecisionMetrics(decisions: WorkspaceDashboardData['decisions']) {
  return {
    activeCount: decisions.length,
    awaitingCount: decisions.filter((d) => d.status === 'AWAITING_APPROVAL').length,
    unresolvedCount: decisions.filter((d) => d.has_unresolved_tasks).length,
    highRiskCount: decisions.filter((d) => d.risk_level === 'HIGH').length,
    medRiskCount: decisions.filter((d) => d.risk_level === 'MEDIUM').length,
    lowRiskCount: decisions.filter((d) => d.risk_level === 'LOW').length,
  };
}

function deriveTaskMetrics(tasks: WorkspaceDashboardData['tasks']) {
  return {
    underReviewCount: tasks.filter((t) => t.status === 'UNDER_REVIEW').length,
    rejectedCount: tasks.filter((t) => t.status === 'REJECTED').length,
    overdueCount: tasks.filter((t) => t.is_overdue).length,
    blockedCount: tasks.filter((t) => t.is_blocked).length,
    decisionLinkedPct: pct(tasks.filter((t) => t.is_decision_linked).length, tasks.length),
  };
}

function deriveOpsMetrics(
  spreadsheets: WorkspaceDashboardData['spreadsheets'],
  patterns: WorkspaceDashboardData['patterns'],
) {
  return {
    activeSheets: spreadsheets.length,
    runningSheets: spreadsheets.filter((s) => s.has_running_job).length,
    activePatterns: patterns.length,
    recentPattern: patterns[0] ?? null,
    lastActivity: spreadsheets[0]?.updated_at ?? null,
  };
}

// ── Main component ────────────────────────────────────────────────────────────

interface Props { projectId: Id; }
export const WORKSPACE_WIDGET_IDS = [
  'overall-progress', 'tasks-completed', 'task-completion-rate', 'overdue-tasks', 'needs-attention',
  'decisions', 'tasks', 'operations',
  'task-status', 'task-priority', 'task-types', 'task-trend',
] as const;
export type WorkspaceWidgetId = (typeof WORKSPACE_WIDGET_IDS)[number];

export function isWorkspaceWidgetId(id: string): id is WorkspaceWidgetId {
  return (WORKSPACE_WIDGET_IDS as readonly string[]).includes(id);
}

interface WorkspaceContextValue {
  workspace: WorkspaceDashboardData | null;
  summary: DashboardSummaryData | null;
  loading: boolean;
  error: string | null;
  trendDays: 7 | 30;
  setTrendDays: (days: 7 | 30) => void;
  trendLoading: boolean;
  projectId: Id;
}
const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

/** Fetch workspace metrics once, even when every card is a separate dashboard widget. */
export function WorkspaceDashboardProvider({ projectId, children }: Props & { children: ReactNode }) {
  const [workspace, setWorkspace] = useState<WorkspaceDashboardData | null>(null);
  const [summary, setSummary] = useState<DashboardSummaryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Trend chart tab: 7 = last 7 days, 30 = last 30 days
  const [trendDays, setTrendDays] = useState<7 | 30>(7);
  // Separate loading state for the trend chart tab switch
  const [trendLoading, setTrendLoading] = useState(false);

  // Initial load: fetch both endpoints in parallel
  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const [ws, sm] = await Promise.all([
          WorkspaceAPI.getWorkspaceDashboard(projectId),
          WorkspaceAPI.getDashboardSummary(projectId, 7),
        ]);
        if (!cancelled) { setWorkspace(ws); setSummary(sm); }
      } catch (err: unknown) {
        if (!cancelled) {
          const e = err as { response?: { data?: { detail?: string } }; message?: string };
          setError(e?.response?.data?.detail || e?.message || 'Could not load workspace data');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [projectId]);

  // Re-fetch summary when trend tab changes (only summary needed, workspace data stays)
  useEffect(() => {
    // Skip on initial mount — initial load already fetches with days=7
    if (!workspace) return;
    let cancelled = false;
    const reload = async () => {
      try {
        setTrendLoading(true);
        const sm = await WorkspaceAPI.getDashboardSummary(projectId, trendDays);
        if (!cancelled) setSummary(sm);
      } catch {
        // Keep existing summary on error — trend chart just stays stale
      } finally {
        if (!cancelled) setTrendLoading(false);
      }
    };
    void reload();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [trendDays]);

  return <WorkspaceContext.Provider value={{ workspace, summary, loading, error, trendDays, setTrendDays, trendLoading, projectId }}>{children}</WorkspaceContext.Provider>;
}

/** One shared message for a failed workspace fetch; each tile only shows a compact placeholder. */
export function WorkspaceDashboardError() {
  const context = useContext(WorkspaceContext);
  if (!context || context.loading || !context.error) return null;
  return (
    <div role="alert" className="mb-3 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
      <strong className="font-semibold">Could not load workspace metrics.</strong> {context.error}
    </div>
  );
}

/** Compact stand-in for one workspace tile while metrics load or after they fail. */
function WorkspaceTilePlaceholder({ section, failed = false }: { section: WorkspaceWidgetId; failed?: boolean }) {
  // Keep the move and remove controls so a tile can still be rearranged or removed.
  const tileControls = useDashboardTileControls(section);
  return (
    <div
      data-testid={failed ? 'workspace-widget-unavailable' : 'workspace-widget-skeleton'}
      aria-busy={!failed}
      style={{ display: 'flex', flexDirection: 'column', gap: 10, height: '100%', minWidth: 0, overflow: 'hidden', background: '#fff', borderRadius: SURFACE_RADIUS, border: `1px solid ${SURFACE_BORDER}`, padding: PANEL_PAD }}
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, height: 28, minWidth: 0 }}>
        {tileControls?.dragHandle}
        {failed
          ? <span style={{ fontSize: 12, color: TEXT_MUTED }}>Data unavailable</span>
          : <span className="animate-pulse" aria-hidden="true" style={{ height: 10, width: '40%', borderRadius: 4, background: '#eef0f2' }} />}
        {tileControls?.removeButton && <span style={{ marginLeft: 'auto' }}>{tileControls.removeButton}</span>}
      </div>
      {!failed && <div className="animate-pulse" aria-hidden="true" style={{ flex: 1, minHeight: 0, borderRadius: 8, background: '#f3f4f6' }} />}
    </div>
  );
}

export default function WorkspaceDashboardWidget({ section }: { section: WorkspaceWidgetId }) {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error('WorkspaceDashboardWidget requires WorkspaceDashboardProvider');
  const { workspace, summary, loading, error, trendDays, setTrendDays, trendLoading, projectId } = context;
  // One fetch feeds every tile, so tiles stay compact here and the shared
  // message is shown once by WorkspaceDashboardError.
  if (loading) return <WorkspaceTilePlaceholder section={section} />;
  if (error) return <WorkspaceTilePlaceholder section={section} failed />;
  if (!workspace || !summary) return null;

  // Derived metrics
  const dec = deriveDecisionMetrics(workspace.decisions);
  const task = deriveTaskMetrics(workspace.tasks);
  const ops = deriveOpsMetrics(workspace.spreadsheets, workspace.patterns);

  const totalTasks = summary.status_overview.total_work_items;
  const doneTasks = summary.status_overview.breakdown.find((b) => b.display_name === 'Done')?.count ?? 0;
  const completionPct = pct(doneTasks, totalTasks);
  const needsAttention = dec.highRiskCount + task.overdueCount + task.blockedCount;
  const maxPriority = Math.max(...summary.priority_breakdown.map((p) => p.count), 1);

  const statusLabels = summary.status_overview.breakdown.map((b) => b.display_name);
  const statusValues = summary.status_overview.breakdown.map((b) => b.count);
  const statusColors = summary.status_overview.breakdown.map((b) => STATUS_COLOR[b.display_name] ?? COLOR.gray);

  const typeLabels = summary.types_of_work.map((t) => t.display_name);
  const typeValues = summary.types_of_work.map((t) => t.count);
  const typeColors = typeLabels.map((_, i) => TYPE_COLORS[i % TYPE_COLORS.length]);

  // Trend chart labels — force English locale to avoid Chinese weekday names.
  // For 7d: show short weekday "Mon", "Tue" etc.
  // For 30d: only show a label every 5 days to avoid crowding; others are empty string.
  const trendLabels = (summary?.daily_task_activity ?? []).map((d, i) => {
    const dt = new Date(d.date);
    if (trendDays === 7) {
      // Short English weekday
      return dt.toLocaleDateString('en-US', { weekday: 'short' });
    }
    // 30d: label every 5th point, blank otherwise
    if (i % 5 === 0) {
      return dt.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }
    return '';
  });
  const trendCreated = (summary?.daily_task_activity ?? []).map((d) => d.created);
  const trendCompleted = (summary?.daily_task_activity ?? []).map((d) => d.completed);

  return (
    <div style={{ height: '100%' }}>
      {/* PROJECT OVERVIEW */}

      {['overall-progress', 'tasks-completed', 'task-completion-rate', 'overdue-tasks', 'needs-attention'].includes(section) && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 210px), 1fr))', gap: 10, height: '100%' }}>

        {section === 'overall-progress' && (<>
        {/* Overall Progress */}
        <SummaryCard
          widgetId={section}
          label="Overall Progress"
          rawValue={completionPct} displayValue={`${completionPct}%`}
          barFill={completionPct} barColor={COLOR.green}
          sub={`${doneTasks} / ${totalTasks} tasks done`}
        />

        </>)}
        {section === 'tasks-completed' && (
          <SummaryCard widgetId={section} label="Tasks Completed" rawValue={doneTasks} sub={`${totalTasks} total tasks`} />
        )}
        {section === 'task-completion-rate' && (<>
        {/* Task Completion Rate */}
        <SummaryCard
          widgetId={section}
          label="Task Completion Rate"
          rawValue={completionPct} displayValue={`${completionPct}%`}
          valueColor={COLOR.green} barFill={completionPct} barColor={COLOR.green}
          sub={`+${summary.time_metrics.completed_last_7_days} completed last 7d`} subColor={COLOR.green}
        />

        </>)}
        {section === 'overdue-tasks' && (<>
        {/* Overdue Tasks */}
        <SummaryCard
          widgetId={section}
          label="Overdue Tasks"
          rawValue={task.overdueCount}
          valueColor={task.overdueCount > 0 ? COLOR.red : TEXT_PRIMARY}
          barFill={pct(task.overdueCount, totalTasks)} barColor={COLOR.red}
          sub={`${pct(task.overdueCount, totalTasks)}% of active tasks`}
        />

        </>)}
        {section === 'needs-attention' && (<>
        {/* Needs Attention */}
        <SummaryCard
          widgetId={section}
          label="Needs Attention"
          rawValue={needsAttention}
          valueColor={needsAttention > 0 ? COLOR.orange : TEXT_PRIMARY}
          barFill={pct(needsAttention, totalTasks)} barColor={COLOR.orange}
          sub={`${dec.highRiskCount} high-risk · ${task.overdueCount} overdue · ${task.blockedCount} blocked`}
        />

        </>)}
      </div>}

      {/* MODULE SUMMARY */}

      {['decisions', 'tasks', 'operations'].includes(section) && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 260px), 1fr))', gap: 10, height: '100%' }}>

        {section === 'decisions' && (<>
        {/* DECISIONS */}
        <ZonePanel
          widgetId={section}
          iconSvg={
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#14b8a6" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <line x1="12" y1="3" x2="12" y2="20" />
              <line x1="4" y1="6" x2="20" y2="6" />
              <line x1="4" y1="6" x2="4" y2="14" />
              <line x1="20" y1="6" x2="20" y2="14" />
              <path d="M1 14h6a3 3 0 0 1-6 0z" />
              <path d="M17 14h6a3 3 0 0 1-6 0z" />
              <line x1="9" y1="20" x2="15" y2="20" />
            </svg>
          }
          iconBg="#ecfeff" title="Decisions" badge=""
          // badge={`${dec.activeCount} active`}
          viewAllHref={nestedProjectPath(projectId, '/decisions')}
        >
          <div style={{ borderBottom: '1px solid #f2f3f5' }}>
            <MetricRow label="Active decisions" value={dec.activeCount} tooltip={`${dec.activeCount} decision${dec.activeCount !== 1 ? 's' : ''} currently active in this project`} />
            <MetricRow label="Awaiting approval" value={dec.awaitingCount} valueColor={dec.awaitingCount > 0 ? COLOR.orange : TEXT_PRIMARY} tooltip={dec.awaitingCount > 0 ? `${dec.awaitingCount} decision${dec.awaitingCount > 1 ? 's' : ''} waiting for approval` : undefined} />
            <MetricRow label="Has unresolved tasks" value={dec.unresolvedCount} valueColor={dec.unresolvedCount > 0 ? COLOR.orange : TEXT_PRIMARY} tooltip={dec.unresolvedCount > 0 ? `${dec.unresolvedCount} decision${dec.unresolvedCount > 1 ? 's' : ''} with open linked tasks` : undefined} />
            <MetricRow label="High risk" value={dec.highRiskCount} valueColor={dec.highRiskCount > 0 ? COLOR.red : TEXT_PRIMARY} tooltip={dec.highRiskCount > 0 ? `${dec.highRiskCount} decision${dec.highRiskCount > 1 ? 's' : ''} flagged as high risk` : undefined} />
          </div>
          <Divider />
          <p style={{ fontSize: 10, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 5 }}>Risk distribution</p>
          <SegBar segments={[
            { flex: dec.highRiskCount, color: RISK_COLOR.HIGH, title: `High ${pct(dec.highRiskCount, dec.activeCount)}%` },
            { flex: dec.medRiskCount, color: RISK_COLOR.MEDIUM, title: `Medium ${pct(dec.medRiskCount, dec.activeCount)}%` },
            { flex: dec.lowRiskCount, color: RISK_COLOR.LOW, title: `Low ${pct(dec.lowRiskCount, dec.activeCount)}%` },
          ]} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 6 }}>
            {dec.highRiskCount > 0 && <span style={{ fontSize: 10, color: '#888' }}>High {pct(dec.highRiskCount, dec.activeCount)}%</span>}
            {dec.medRiskCount > 0 && <span style={{ fontSize: 10, color: '#888' }}>Medium {pct(dec.medRiskCount, dec.activeCount)}%</span>}
            {dec.lowRiskCount > 0 && <span style={{ fontSize: 10, color: '#888' }}>Low {pct(dec.lowRiskCount, dec.activeCount)}%</span>}
          </div>
        </ZonePanel>

        </>)}
        {section === 'tasks' && (<>
        {/* TASKS */}
        <ZonePanel
          widgetId={section}
          iconSvg={
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="9 11 12 14 22 4" />
              <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
            </svg>
          }
          iconBg="#fffbeb" title="Tasks" badge=""
          // badge={`${workspace.tasks.length} priority queue`}
          viewAllHref={nestedProjectPath(projectId, '/tasks')}
        >
          <div style={{ borderBottom: '1px solid #f2f3f5' }}>
            <MetricRow label="Completion rate" value={`${completionPct}%`} valueColor={completionPct >= 50 ? COLOR.green : TEXT_PRIMARY} tooltip={`${doneTasks} of ${totalTasks} tasks completed`} />
            <MetricRow
              label="Under review"
              value={task.underReviewCount}
              valueColor={task.underReviewCount > 0 ? COLOR.blue : TEXT_PRIMARY}
              tooltip={
                task.underReviewCount > 0
                  ? `${task.underReviewCount} task${task.underReviewCount > 1 ? 's' : ''} awaiting approval`
                  : 'No tasks under review'
              }
            />
            <MetricRow
              label="Rejected"
              value={task.rejectedCount}
              valueColor={task.rejectedCount > 0 ? COLOR.orange : TEXT_PRIMARY}
              tooltip={
                task.rejectedCount > 0
                  ? `${task.rejectedCount} task${task.rejectedCount > 1 ? 's' : ''} rejected and requiring rework`
                  : 'No rejected tasks'
              }
            />
            <MetricRow
              label="Blocked"
              value={task.blockedCount}
              valueColor={task.blockedCount > 0 ? COLOR.red : TEXT_PRIMARY}
              tooltip={
                task.blockedCount > 0
                  ? `${task.blockedCount} task${task.blockedCount > 1 ? 's' : ''} blocked by dependencies`
                  : 'No blocked tasks'
              }
            />
            <MetricRow label="Due soon (7d)" value={summary.time_metrics.due_soon} valueColor={summary.time_metrics.due_soon > 0 ? COLOR.orange : TEXT_PRIMARY} tooltip={`${summary.time_metrics.due_soon} task${summary.time_metrics.due_soon !== 1 ? 's' : ''} due within the next 7 days`} />
            <MetricRow label="Decision-linked" value={`${task.decisionLinkedPct}%`} tooltip="Tasks linked to a committed decision" />
            <MetricRow label="Created last 7d" value={summary.time_metrics.created_last_7_days} tooltip="New tasks created in the past 7 days" />
            <MetricRow label="Completed last 7d" value={summary.time_metrics.completed_last_7_days} valueColor={COLOR.green} tooltip="Tasks approved or locked in the past 7 days" />
          </div>
          <Divider />
          <p style={{ fontSize: 10, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 5 }}>Status pipeline</p>
          <SegBar segments={summary.status_overview.breakdown.map((b) => ({
            flex: b.count, color: STATUS_COLOR[b.display_name] ?? COLOR.gray,
            title: `${b.display_name} ${pct(b.count, totalTasks)}%`,
          }))} />
          <p style={{ fontSize: 10, color: '#bbb', marginTop: 5 }}>Hover to see breakdown · full chart below</p>
        </ZonePanel>

        </>)}
        {section === 'operations' && (<>
        {/* OPERATIONS */}
        <ZonePanel
          widgetId={section}
          iconSvg={
            <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#22c55e" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="3" width="7" height="7" rx="1" />
              <rect x="14" y="3" width="7" height="7" rx="1" />
              <rect x="14" y="14" width="7" height="7" rx="1" />
              <rect x="3" y="14" width="7" height="7" rx="1" />
            </svg>
          }
          iconBg="#ecfdf5" title="Operations" badge=""
          // badge={`${ops.activeSheets + ops.activePatterns} active`}
          viewAllHref={nestedProjectPath(projectId, '/spreadsheets')}
        >
          <div style={{ borderBottom: '1px solid #f2f3f5' }}>
            <MetricRow label="Active spreadsheets" value={ops.activeSheets} tooltip={`${ops.activeSheets} spreadsheet${ops.activeSheets !== 1 ? 's' : ''} in this project`} />
            <MetricRow label="Sheets with running jobs" value={ops.runningSheets} valueColor={ops.runningSheets > 0 ? COLOR.green : TEXT_PRIMARY} tooltip={ops.runningSheets > 0 ? `${ops.runningSheets} sheet${ops.runningSheets > 1 ? 's' : ''} currently processing a job` : 'No sheets have active jobs'} />
            <MetricRow label="Active patterns" value={ops.activePatterns} tooltip={`${ops.activePatterns} workflow pattern${ops.activePatterns !== 1 ? 's' : ''} available`} />
            <MetricRow label="Recently used pattern" value={ops.recentPattern ? `${ops.recentPattern.name} v${ops.recentPattern.version}` : '—'} tooltip={ops.recentPattern ? `Last updated ${relTime(ops.recentPattern.updated_at)}` : undefined} />
            <MetricRow label="Last activity" value={relTime(ops.lastActivity)} tooltip={ops.lastActivity ? `Most recently updated sheet: ${new Date(ops.lastActivity).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}` : undefined} />
          </div>
          <Divider />
          <p style={{ fontSize: 10, color: '#aaa', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: 2 }}>Active sheets — last 7 days</p>
          <SheetMiniBarChart spreadsheets={workspace.spreadsheets} />
        </ZonePanel>

        </>)}
      </div>}

      {/* CHART ROW */}
      {['task-status', 'task-priority', 'task-types'].includes(section) && <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 320px), 1fr))', gap: 12, height: '100%' }}>
        {section === 'task-status' && <ChartPanel widgetId={section} section="Tasks" title="Status breakdown">
          <HorizontalBarChart labels={statusLabels} values={statusValues} colors={statusColors} />
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginTop: 10 }}>
            {summary.status_overview.breakdown.map((b) => (
              <LegendItem
                key={b.display_name}
                color={STATUS_COLOR[b.display_name] ?? COLOR.gray}
                label={`${b.display_name} ${b.count}`}
                tooltip={STATUS_EXPLANATION[b.display_name] ?? `${b.display_name}: ${b.count} task${b.count === 1 ? '' : 's'}`}
              />
            ))}
          </div>
        </ChartPanel>}
        {section === 'task-priority' && <ChartPanel widgetId={section} section="Tasks" title="Priority distribution">
          <div style={{ padding: '2px 0 10px' }}>
            {PRIORITY_CONFIG.map((p) => {
              const count = summary.priority_breakdown.find((b) => b.priority === p.key)?.count ?? 0;
              return <PriorityRow key={p.key} label={p.label} count={count} max={maxPriority} color={p.color} />;
            })}
          </div>
        </ChartPanel>}
        {section === 'task-types' && <ChartPanel widgetId={section} section="Tasks" title="Type breakdown">
          <DonutChart labels={typeLabels} values={typeValues} colors={typeColors} />
        </ChartPanel>}
      </div>}

      {/* TREND */}
      {section === 'task-trend' && <ChartPanel
        widgetId={section}
        section="Tasks" title="Created vs Completed — last 7 days" full
        right={
          <div style={{ display: 'flex', gap: 4, alignItems: 'center' }}>
            {trendLoading && (
              <span style={{ fontSize: 10, color: '#aaa', marginRight: 4 }}>Loading…</span>
            )}
            {([7, 30] as const).map((d) => (
              <button
                key={d}
                type="button"
                onClick={() => setTrendDays(d)}
                style={{
                  fontSize: 10, padding: '2px 8px',
                  border: '1px solid #e8eaed', borderRadius: 5,
                  background: trendDays === d ? '#0f766e' : 'transparent',
                  color: trendDays === d ? '#fff' : '#aaa',
                  cursor: 'pointer', fontFamily: 'inherit',
                  transition: 'all 0.15s',
                }}
              >
                {d}d
              </button>
            ))}
          </div>
        }
      >
        <LineChart
          labels={trendLabels}
          series={[
            { label: 'Created', values: trendCreated, color: COLOR.blue, dashed: true },
            { label: 'Completed', values: trendCompleted, color: COLOR.green },
          ]}
        />
        <div style={{ display: 'flex', gap: 10, marginTop: 6 }}>
          <LegendItem color={COLOR.blue} label="Created" />
          <LegendItem color={COLOR.green} label="Completed" />
        </div>
      </ChartPanel>}
    </div>
  );
}
