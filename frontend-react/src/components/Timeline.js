/**
 * Timeline.js – Processing iteration timeline with pass/fail badges
 */
import React from 'react';
import { useApp } from '../App';

export default function Timeline() {
    const { runState } = useApp();
    const iterations = runState.live?.iterations || runState.result?.processing_timeline || [];

    if (!iterations.length) {
        return (
            <div className="card timeline-card">
                <h3>🕐 Processing Timeline</h3>
                <div className="glow-divider" />
                <p style={{ color: 'var(--text-muted)', fontSize: '0.85rem' }}>Timeline will populate as the agent runs…</p>
            </div>
        );
    }

    const total = 5; // max iterations

    return (
        <div className="card timeline-card">
            <div className="timeline-header">
                <h3>🕐 Processing Timeline</h3>
                <span className="badge badge-blue">
                    {iterations.length}/{total} iterations
                </span>
            </div>
            <div className="glow-divider" />

            <div className="timeline-list">
                {iterations.map((iter, idx) => {
                    const isPassed = iter.status === 'PASS' || iter.status === 'success' || iter.status === 'PROCESS';
                    const ts = iter.timestamp ? new Date(iter.timestamp).toLocaleTimeString() : '—';

                    return (
                        <div key={idx} className={`tl-item ${isPassed ? 'tl-pass' : 'tl-fail'}`}>
                            {/* Connector */}
                            <div className="tl-connector">
                                <div className={`tl-dot ${isPassed ? 'dot-green' : 'dot-red'}`} />
                                {idx < iterations.length - 1 && <div className="tl-line" />}
                            </div>

                            {/* Content */}
                            <div className="tl-content">
                                <div className="tl-top">
                                    <span className="tl-iter">Iteration {iter.iteration}</span>
                                    <span className={`badge ${isPassed ? 'badge-green' : 'badge-red'}`}>
                                        {iter.status === 'PROCESS' ? '▶ PROCESSING' : isPassed ? '✓ PASS' : '✗ FAIL'}
                                    </span>
                                </div>
                                <p className="tl-msg">{iter.message}</p>
                                <div className="tl-meta">
                                    <span>🕐 {ts}</span>
                                    {(iter.failures_count > 0 || iter.problems_count > 0) && (
                                        <span style={{ color: 'var(--accent-orange)' }}>
                                            ⚠ {iter.problems_count || iter.failures_count} problem(s)
                                        </span>
                                    )}
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>
        </div>
    );
}


