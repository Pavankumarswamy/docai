/**
 * SummaryCard.js – Run summary with Document Processing status badge
 */
import React from 'react';
import { useApp } from '../App';

export default function SummaryCard() {
    const { runState } = useApp();
    const { repoUrl, teamName, leaderName, branchName, status, live, result } = runState;

    const summary = result?.run_summary;
    const procStatus = summary?.final_status || (status === 'running' ? 'RUNNING' : '—');

    const statusBadge = {
        PASSED: { cls: 'badge-green', icon: '✓', label: 'COMPLETED' },
        FAILED: { cls: 'badge-red', icon: '✗', label: 'FAILED' },
        RUNNING: { cls: 'badge-blue', icon: '⟳', label: 'PROCESSING' },
        '—': { cls: 'badge-gray', icon: '—', label: 'PENDING' },
    }[procStatus] || { cls: 'badge-gray', icon: '—', label: procStatus };

    return (
        <div className="card summary-card">
            <div className="summary-header">
                <h3>📋 Run Summary</h3>
                <span className={`badge ${statusBadge.cls}`}>
                    {statusBadge.icon} {statusBadge.label}
                </span>
            </div>
            <div className="glow-divider" />

            <div className="summary-grid">
                <SummaryRow label="Folder" value={repoUrl || '—'} mono link={repoUrl} />
                <SummaryRow label="Team" value={teamName || '—'} />
                <SummaryRow label="Owner" value={leaderName || '—'} />
                <SummaryRow label="Session" value={branchName || '—'} mono highlight />
                <SummaryRow label="Phase" value={live?.phase ? `[${live?.phase?.toUpperCase()}]` : '—'} />
                {summary && <>
                    <SummaryRow label="Problems Found" value={summary.problems_found ?? '—'} />
                    <SummaryRow label="Edits Applied" value={summary.edits_applied ?? '—'} color="green" />
                    <SummaryRow label="Edits Failed" value={summary.edits_failed ?? '—'} color="red" />
                    <SummaryRow label="Total Time" value={summary.total_time_human || '—'} />
                </>}
            </div>
        </div>
    );
}

function SummaryRow({ label, value, mono, link, highlight, color }) {
    const colorMap = { green: 'var(--accent-green)', red: 'var(--accent-red)' };
    const style = color ? { color: colorMap[color], fontWeight: 600 }
        : highlight ? { color: 'var(--accent-cyan)', fontWeight: 600 }
            : {};
    return (
        <div className="summary-row">
            <span className="summary-label">{label}</span>
            {link && link.startsWith('http')
                ? <a href={link} target="_blank" rel="noreferrer" className={`summary-value mono`} style={{ color: 'var(--accent-blue)', wordBreak: 'break-all' }}>{value}</a>
                : <span className={`summary-value ${mono ? 'mono' : ''}`} style={{ wordBreak: 'break-all', ...style }}>{String(value)}</span>
            }
        </div>
    );
}


