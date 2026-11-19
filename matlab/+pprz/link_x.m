function link_x(axs)
%LINK_X Keep the time range (XLim) of these axes the same: zooming or panning one moves the others.
%   pprz.link_x(pprz.time_axes(tab))
%   A light-weight linkaxes(axs, 'x') for pprz_view: linkaxes on axes that are then moved into an app window
%   (uifigure tab) costs seconds per plot; a listener per axis does not. Falls back to linkaxes where
%   listeners on XLim are not available (Octave).
    axs = axs(isgraphics(axs));
    if numel(axs) < 2
        return
    end
    try
        for k = 1:numel(axs)
            old = getappdata(axs(k), 'pprz_link_x');
            if ~isempty(old) && isvalid(old)
                delete(old);
            end
            setappdata(axs(k), 'pprz_link_x', addlistener(axs(k), 'XLim', 'PostSet', @(~, ~) follow(axs(k), axs)));
        end
    catch
        linkaxes(axs, 'x');
    end
end

function follow(source, axs)
    x = get(source, 'XLim');
    for k = 1:numel(axs)
        if axs(k) ~= source && isgraphics(axs(k)) && ~isequal(get(axs(k), 'XLim'), x)
            set(axs(k), 'XLim', x);  % its own listener then finds every axis already equal
        end
    end
end
