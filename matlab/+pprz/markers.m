function markers(f, figs, kinds)
%MARKERS Draw flight events as labelled vertical lines on every time plot of the figures.
%   pprz.markers(f, figs)                      mode changes, setting changes, checklist items, INFO_MSG
%   pprz.markers(f, figs, {'setting'})         only these kinds (see pprz.events)
%   Labels are written on the top plot of each figure; click a marker to show its full text, click it
%   again to hide it.
    if nargin < 3
        ev = pprz.events(f);
    else
        ev = pprz.events(f, kinds);
    end
    colors = struct('mode', [0.2 0.4 0.8], 'setting', [0.85 0.4 0], 'checklist', [0.1 0.55 0.3], ...
        'message', [0.45 0.45 0.45]);
    for fig = reshape(figs, 1, [])
        axs = pprz.time_axes(fig);
        for a = 1:numel(axs)
            ax = axs(a);
            delete(findall(ax, 'Tag', 'pprz_event'));
            set(ax, 'YLimMode', 'manual');  % the markers span the shown limits and do not change them
            y = get(ax, 'YLim');
            x = get(ax, 'XLim');
            last = -Inf;
            for k = 1:numel(ev)
                e = ev(k);
                if e.t < x(1) || e.t > x(2)
                    continue
                end
                c = colors.(e.kind);
                line([e.t e.t], y, 'Parent', ax, 'Color', c, 'LineStyle', '--', 'LineWidth', 0.75, ...
                    'Tag', 'pprz_event', 'HandleVisibility', 'off', 'UserData', e.text, ...
                    'ButtonDownFcn', @(h, ~) toggle(h));
                % Labels only on the top plot, and not when they would overlap the previous one.
                if a == 1 && e.t - last > 0.02 * diff(x)
                    text(e.t, y(1) + 0.98 * diff(y), [e.label ' '], 'Parent', ax, 'Rotation', 90, 'Color', c * 0.8, ...
                        'FontSize', 8, 'HorizontalAlignment', 'right', 'VerticalAlignment', 'bottom', ...
                        'Clipping', 'on', 'Interpreter', 'none', 'Tag', 'pprz_event', 'HandleVisibility', 'off', ...
                        'HitTest', 'off');
                    last = e.t;
                end
            end
        end
    end
end

function toggle(h)
    ax = get(h, 'Parent');
    open = findall(ax, 'Tag', 'pprz_event_text');
    mine = ~isempty(open) && isequal(get(open(1), 'UserData'), h);
    delete(open);
    if mine
        return
    end
    x = get(h, 'XData');
    y = get(ax, 'YLim');
    text(x(1), y(1) + 0.95 * diff(y), get(h, 'UserData'), 'Parent', ax, 'BackgroundColor', [1 1 0.9], ...
        'EdgeColor', [0.6 0.6 0.6], 'FontSize', 9, 'VerticalAlignment', 'top', 'Interpreter', 'none', ...
        'Tag', 'pprz_event_text', 'UserData', h, 'HandleVisibility', 'off', 'HitTest', 'off');
end
