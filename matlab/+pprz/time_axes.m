function axs = time_axes(fig)
%TIME_AXES The axes of a figure that plot against time: 2-D, linear x, and an x label that is empty or
%   mentions time (so spectra, maps and 3-D tracks are left out).
%   axs = pprz.time_axes(fig)
    axs = findobj(fig, 'Type', 'axes');
    keep = false(size(axs));
    for k = 1:numel(axs)
        label = lower(strtrim(strjoin(cellstr(get(get(axs(k), 'XLabel'), 'String')), ' ')));
        v = get(axs(k), 'View');
        keep(k) = ~strcmp(get(axs(k), 'Tag'), 'legend') && strcmp(get(axs(k), 'XScale'), 'linear') && v(1) == 0 && v(2) == 90 && ...
            isnumeric(get(axs(k), 'XLim')) && (isempty(label) || ~isempty(strfind(label, 'time')));
    end
    axs = axs(keep);
    if isempty(axs)
        axs = reshape(axs, 1, 0);
        return
    end
    pos = cell2mat(get(axs, {'Position'}));  % top to bottom, then left to right
    [~, order] = sortrows([-pos(:, 2), pos(:, 1)]);
    axs = reshape(axs(order), 1, []);
end
