function markers(f, figs, kinds)
%MARKERS Draw flight events as labelled vertical lines on every time plot of the figures.
%   pprz.markers(f, figs)                      mode changes, setting changes, checklist items, INFO_MSG
%   pprz.markers(f, figs, {'setting'})         only these kinds (see pprz.events); {} removes the markers
%   Labels are written on the top plot of each figure; click a marker to show its full text, click it
%   again to hide it. Calling it again replaces the markers; zooming or panning lays the labels out again.
    if nargin < 3
        ev = pprz.events(f);
    else
        ev = pprz.events(f, kinds);
    end
    for fig = reshape(figs, 1, [])
        draw(ev, fig);
        old = getappdata(fig, 'pprz_event_listener');
        if ~isempty(old) && isvalid(old)
            delete(old);
        end
        axs = pprz.time_axes(fig);
        if ~isempty(ev) && ~isempty(axs)
            try  % MATLAB only; the time axes are linked, so the top one is enough
                setappdata(fig, 'pprz_event_listener', ...
                    addlistener(axs(1), 'XLim', 'PostSet', @(~, ~) draw(ev, fig)));
            catch
            end
        end
    end
end

function draw(ev, fig)
    colors = struct('mode', [0.2 0.4 0.8], 'setting', [0.85 0.4 0], 'checklist', [0.1 0.55 0.3], ...
        'message', [0.45 0.45 0.45]);
    if ~isgraphics(fig)
        return
    end
    axs = pprz.time_axes(fig);
    for a = 1:numel(axs)
        ax = axs(a);
        delete(findall(ax, 'Tag', 'pprz_event'));
        delete(findall(ax, 'Tag', 'pprz_event_text'));
        if isempty(ev)
            continue
        end
        set(ax, 'YLimMode', 'manual');  % the markers span the shown limits and do not change them
        y = get(ax, 'YLim');
        x = get(ax, 'XLim');
        last = -Inf;
        px = getpixelposition(ax);
        fit = max(6, floor(0.85 * px(4) / (8 * 0.7)));  % characters of 8 pt text that fit the axes height
        for k = 1:numel(ev)
            e = ev(k);
            if e.t < x(1) || e.t > x(2)
                continue
            end
            c = colors.(e.kind);
            line([e.t e.t], y, 'Parent', ax, 'Color', c, 'LineStyle', '--', 'Marker', 'none', 'LineWidth', 0.75, ...
                'Tag', 'pprz_event', 'HandleVisibility', 'off', 'UserData', e.text, ...
                'ButtonDownFcn', @(h, ~) toggle(h));
            % Labels only on the top plot, and not when they would overlap the previous one.
            if a == 1 && e.t - last > 0.02 * diff(x)
                % Rotated text: 'bottom' puts it left of the line, 'top' right of it; keep it inside the axes.
                side = 'bottom';
                if e.t < x(1) + 0.5 * diff(x)
                    side = 'top';
                end
                label = e.label;
                if numel(label) > fit
                    label = [label(1:fit - 1) '…'];
                end
                text(e.t, y(1) + 0.98 * diff(y), [label ' '], 'Parent', ax, 'Rotation', 90, 'Color', c * 0.8, ...
                    'FontSize', 8, 'HorizontalAlignment', 'right', 'VerticalAlignment', side, ...
                    'Clipping', 'on', 'Interpreter', 'none', 'Tag', 'pprz_event', 'HandleVisibility', 'off', ...
                    'HitTest', 'off');
                last = e.t;
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
