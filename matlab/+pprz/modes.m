function modes(f, fig, source)
%MODES Shade mode regions behind every axes of a figure.
%   pprz.modes(f, fig, 'ap')   autopilot mode (ROTORCRAFT_STATUS.ap_mode, named from the protocol)
%   pprz.modes(f, fig, 'rc')   RC mode switch (ROTORCRAFT_RADIO_CONTROL.mode)
    switch source
        case 'ap'
            [msg, field] = deal('ROTORCRAFT_STATUS', 'ap_mode');
        case 'rc'
            [msg, field] = deal('ROTORCRAFT_RADIO_CONTROL', 'mode');
        otherwise
            return
    end
    if ~pprz.has(f.data, msg)
        return
    end
    t = f.data.(msg).timestamp;
    v = double(f.data.(msg).(field));
    change = [1; find(diff(v) ~= 0) + 1];
    stops = [t(change(2:end)); t(end)];
    values = unique(v);
    colors = lines(numel(values));
    labels = pprz.enum(f, msg, field, values);
    for ax = findobj(fig, 'Type', 'axes')'
        if ~strcmp(ax.XScale, 'linear') || isa(ax.XAxis, 'matlab.graphics.axis.decorator.DatetimeRuler')
            continue
        end
        y = ax.YLim;
        ax.YLimMode = 'manual';
        hold(ax, 'on');
        for i = 1:numel(change)
            c = colors(values == v(change(i)), :);
            p = patch(ax, [t(change(i)) stops(i) stops(i) t(change(i))], [y(1) y(1) y(2) y(2)], c, ...
                'FaceAlpha', 0.1, 'EdgeColor', 'none', 'HandleVisibility', 'off', 'HitTest', 'off');
            uistack(p, 'bottom');
            if stops(i) - t(change(i)) > 0.05 * (t(end) - t(1))
                text(ax, t(change(i)), y(2), " " + labels(values == v(change(i))), 'Color', c * 0.7, ...
                    'VerticalAlignment', 'top', 'FontSize', 8, 'Clipping', 'on', 'HitTest', 'off');
            end
        end
    end
end
