function axs = tiles(n, name)
%TILES n stacked time axes for plotters: hold and grid on, 'Time [s]' under the last. pprz_plot links their
%   x axes after drawing (linking before would freeze the limits).
%   axs = pprz.tiles(3, 'Guidance')    name (optional) titles the figure
    try
        layout = tiledlayout(n, 1, 'Padding', 'compact', 'TileSpacing', 'compact');
        if nargin > 1
            title(layout, name);
        end
        axs = gobjects(1, n);
        for k = 1:n
            axs(k) = nexttile(layout);
        end
    catch  % Octave
        axs = zeros(1, n);
        for k = 1:n
            axs(k) = subplot(n, 1, k);
        end
    end
    for k = 1:n
        hold(axs(k), 'on');
        grid(axs(k), 'on');
    end
    xlabel(axs(n), 'Time [s]');  % pprz_plot links the time axes once they are drawn
end
