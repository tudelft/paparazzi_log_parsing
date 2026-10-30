function plot_actuators(ac_data, idx)
%PLOT_ACTUATORS Actuator commands by name (airframe servo names when the export has them), one panel per
%   kind of actuator (names that differ only in their number, such as MOTOR_1 … MOTOR_12).
%   plot_actuators(f.data)       all actuators
%   plot_actuators(f.data, idx)  only columns idx
    [values, names, t] = pprz.actuators(ac_data);
    if nargin < 2
        idx = 1:size(values, 2);
    end
    values = values(:, idx);
    names = names(idx);
    groups = regexprep(names, '_?\d+$', '');
    kinds = unique(groups, 'stable');
    if numel(kinds) > 4
        kinds = {''};  % too many kinds: one panel
        groups(:) = {''};
    end
    axs = pprz.tiles(numel(kinds), 'Actuators');
    for k = 1:numel(kinds)
        in = strcmp(groups, kinds{k});
        pprz.series(axs(k), t, values(:, in), names(in));
        ylabel(axs(k), strtrim(sprintf('%s [pprz]', kinds{k})), 'Interpreter', 'none');
    end
end
