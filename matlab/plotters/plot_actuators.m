function plot_actuators(ac_data, idx)
%PLOT_ACTUATORS Actuator commands by name (airframe servo names when the export has them).
%   plot_actuators(f.data)       all actuators
%   plot_actuators(f.data, idx)  only columns idx
    if ~pprz.has(ac_data, 'ACTUATORS')
        return
    end
    [values, names, t] = pprz.actuators(ac_data);
    if nargin < 2
        idx = 1:size(values, 2);
    end
    plot(t, values(:, idx));
    grid on;
    title('Actuators');
    xlabel('Time [s]');
    ylabel('Command [pprz]');
    legend(strrep(names(idx), '_', '\_'), 'Location', 'eastoutside');
end
