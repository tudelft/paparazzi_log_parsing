function plot_link_stats(ac_data)
%PLOT_LINK_STATS Link statistics per node on the path to the ground (LINK_STATS, pprzlink 2026-11; hop 0 is the
%   autopilot, 1 the next node such as a modem or router): signal, frames rejected per report, the longest gap
%   without frames, and the node's battery and temperature. Panels without data are left out.
    m = ac_data.LINK_STATS;
    hops = unique(double(m.hop(:)))';
    has = @(n) isfield(m, n);
    panels = {};
    if has('rssi') || has('rssi_ac'), panels{end + 1} = 'rssi'; end
    if has('down_rx_err') || has('up_rx_err'), panels{end + 1} = 'errors'; end
    if has('down_max_gap') || has('up_max_gap'), panels{end + 1} = 'gaps'; end
    if has('battery'), panels{end + 1} = 'battery'; end
    if has('temperature'), panels{end + 1} = 'temperature'; end
    axs = pprz.tiles(max(1, numel(panels)), 'Link statistics (LINK_STATS)');
    for h = hops
        rows = double(m.hop(:)) == h;
        t = m.t(rows);
        node = ifelse(h == 0, 'Autopilot', sprintf('Node %d', h));
        for k = 1:numel(panels)
            ax = axs(k);
            switch panels{k}
                case 'rssi'
                    line_(ax, t, value(m, 'rssi', rows, 127), [node ' to ground']);
                    line_(ax, t, value(m, 'rssi_ac', rows, 127), [node ' to aircraft']);
                    ylabel(ax, 'Signal [dBm]');
                case 'errors'
                    for f = {'down_rx_err', 'up_rx_err', 'down_tx_err', 'up_tx_err'}
                        if has(f{1})
                            line_(ax, t, pprz.counter_steps(m.(f{1})(rows), 65536), ...
                                [node ' ' strrep(f{1}, '_', ' ')]);
                        end
                    end
                    ylabel(ax, 'Frames rejected per report');
                case 'gaps'
                    line_(ax, t, value(m, 'down_max_gap', rows, []) / 1000, [node ' downlink']);
                    line_(ax, t, value(m, 'up_max_gap', rows, []) / 1000, [node ' uplink']);
                    ylabel(ax, 'Longest gap [s]');
                case 'battery'
                    line_(ax, t, value(m, 'battery', rows, 255), node);
                    ylabel(ax, 'Battery [%]');
                case 'temperature'
                    line_(ax, t, value(m, 'temperature', rows, 127), node);
                    ylabel(ax, 'Temperature [°C]');
            end
        end
    end
    for k = 1:numel(panels)
        legend(axs(k), 'Location', 'eastoutside');
    end
end

function v = value(m, name, rows, missing)
    % A field of the message for these rows; the "not available" value as NaN; all NaN when not logged.
    if ~isfield(m, name)
        v = nan(nnz(rows), 1);
        return
    end
    v = double(m.(name)(rows));
    v = v(:);
    if ~isempty(missing)
        v(v == missing) = NaN;
    end
end

function line_(ax, t, v, name)
    if any(~isnan(v))
        plot(ax, t, v, 'DisplayName', name);
    end
end

function out = ifelse(c, a, b)
    if c, out = a; else, out = b; end
end
