function series(ax, t, Y, names, varargin)
%SERIES Plot the columns of Y against t in ax with a name each and a legend (outside when long).
%   pprz.series(ax, t, Y, {'East', 'North'})        extra arguments go to plot, e.g. '--'
    Y = double(Y);
    names = cellstr(names);
    for k = 1:size(Y, 2)
        style = {};
        if size(Y, 2) > 7  % the default colour order repeats after 7
            styles = {'-', '--', ':', '-.'};
            style = {'LineStyle', styles{floor((k - 1) / 7) + 1}};
        end
        plot(ax, t, Y(:, k), varargin{:}, style{:}, 'DisplayName', names{min(k, numel(names))});
    end
    if size(Y, 2) > 6
        legend(ax, 'Location', 'eastoutside', 'Interpreter', 'none');
    elseif size(Y, 2) > 1
        legend(ax, 'Location', 'best', 'Interpreter', 'none');
    end
end
