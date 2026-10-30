function plot_commands(ac_data)
%PLOT_COMMANDS The command values sent to the actuators' mixing (COMMANDS, pprz units).
    m = ac_data.COMMANDS;
    values = double(m.values);
    names = arrayfun(@(j) sprintf('Command %d', j - 1), 1:size(values, 2), 'UniformOutput', false);
    used = any(values ~= values(1, :), 1);  % commands that never change are left out
    ax = pprz.tiles(1, sprintf('Commands (%d of %d change)', nnz(used), numel(used)));
    pprz.series(ax, m.t, values(:, used), names(used));
    ylabel(ax, 'Command [pprz]');
end
