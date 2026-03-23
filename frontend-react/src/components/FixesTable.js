/**
 * FixesTable.js – Color-coded table of all fixes applied
 * Uses @tanstack/react-table v8
 */
import React, { useMemo } from 'react';
import {
    useReactTable,
    getCoreRowModel,
    getSortedRowModel,
    flexRender,
    createColumnHelper,
} from '@tanstack/react-table';
import { useApp } from '../App';

const columnHelper = createColumnHelper();

const BUG_COLORS = {
    LINTING: '#f59e0b',
    SYNTAX: '#ef4444',
    LOGIC: '#8b5cf6',
    TYPE_ERROR: '#22d3ee',
    IMPORT: '#4f8ef7',
    INDENTATION: '#10b981',
};

export default function FixesTable() {
    const { runState } = useApp();
    const fixes = runState.result?.fixes_table || [];

    const columns = useMemo(() => [
        columnHelper.accessor('file', {
            header: 'File',
            cell: info => <span className="mono fix-file">{info.getValue()}</span>,
        }),
        columnHelper.accessor('bug_type', {
            header: 'Bug Type',
            cell: info => {
                const bt = info.getValue();
                return (
                    <span className="bug-badge" style={{ background: `${BUG_COLORS[bt] || '#6b7280'}22`, color: BUG_COLORS[bt] || '#6b7280', border: `1px solid ${BUG_COLORS[bt] || '#6b7280'}44` }}>
                        {bt}
                    </span>
                );
            },
        }),
        columnHelper.accessor('line', {
            header: 'Line',
            cell: info => <span className="mono" style={{ color: 'var(--accent-yellow)' }}>:{info.getValue() || '—'}</span>,
        }),
        columnHelper.accessor('problem', {
            header: 'Problem Description',
            cell: info => <span style={{ fontSize: '0.8rem', color: 'var(--text-secondary)' }}>{info.getValue() || '—'}</span>,
        }),
        columnHelper.accessor('status', {
            header: 'Status',
            cell: info => {
                const s = info.getValue();
                return s === 'fixed'
                    ? <span className="badge badge-green">✓ Fixed</span>
                    : <span className="badge badge-red">✗ Failed</span>;
            },
        }),
        columnHelper.accessor('agent', {
            header: 'Agent',
            cell: info => <span style={{ fontSize: '0.75rem', fontWeight: '800', color: 'var(--accent-cyan)', whiteSpace: 'nowrap' }}>{info.getValue() || '—'}</span>,
        }),
    ], []);

    const table = useReactTable({
        data: fixes,
        columns,
        getCoreRowModel: getCoreRowModel(),
        getSortedRowModel: getSortedRowModel(),
    });

    if (!fixes.length) return null;

    return (
        <div className="card fixes-card">
            <div className="fixes-header">
                <h3>🔧 Fixes Applied</h3>
                <span className="badge badge-blue">{fixes.length} total</span>
            </div>
            <div className="glow-divider" />

            <div className="table-scroll">
                <table className="fixes-table">
                    <thead>
                        {table.getHeaderGroups().map(hg => (
                            <tr key={hg.id}>
                                {hg.headers.map(h => (
                                    <th key={h.id} onClick={h.column.getToggleSortingHandler()} className="th-sortable">
                                        {flexRender(h.column.columnDef.header, h.getContext())}
                                        {{ asc: ' ↑', desc: ' ↓' }[h.column.getIsSorted()] ?? ''}
                                    </th>
                                ))}
                            </tr>
                        ))}
                    </thead>
                    <tbody>
                        {table.getRowModel().rows.map((row, idx) => (
                            <tr key={row.id} className={idx % 2 === 0 ? 'row-even' : 'row-odd'}>
                                {row.getVisibleCells().map(cell => (
                                    <td key={cell.id}>
                                        {flexRender(cell.column.columnDef.cell, cell.getContext())}
                                    </td>
                                ))}
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </div>
    );
}

/* Inline styles */
const _s = document.createElement('style');
_s.textContent = `
  .fixes-card { padding: 18px; }
  .fixes-header { display: flex; align-items: center; justify-content: space-between; }
  .table-scroll { overflow-x: auto; margin-top: 4px; }
  .fixes-table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
  .fixes-table th { padding: 8px 10px; text-align: left; font-size: 0.72rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted); border-bottom: 1px solid var(--border-bright); white-space: nowrap; }
  .th-sortable { cursor: pointer; user-select: none; }
  .th-sortable:hover { color: var(--text-primary); }
  .fixes-table td { padding: 8px 10px; vertical-align: middle; border-bottom: 1px solid var(--border); }
  .row-even td { background: transparent; }
  .row-odd  td { background: rgba(255,255,255,0.015); }
  .fixes-table tr:hover td { background: var(--bg-card-hover); }
  .fix-file { font-size: 0.78rem; color: var(--accent-cyan); max-width: 180px; display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bug-badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.5px; white-space: nowrap; }
`;
document.head.appendChild(_s);
