function plot_commands(ac_data)
%PLOT_COMMANDS The command values sent to the actuators' mixing (COMMANDS, pprz units).
    m = ac_data.COMMANDS;
    names = arrayfun(@(j) sprintf('Command %d', j - 1), 1:size(m.values, 2), 'UniformOutput', false);
    ax = pprz.tiles(1, 'Commands');
    pprz.series(ax, m.t, double(m.values), names);
    ylabel(ax, 'Command [pprz]');
end
