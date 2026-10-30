function plot_eff_mat(ac_data)
%PLOT_EFF_MAT Effectiveness per actuator from EFF_MAT_G: thrust, roll, pitch and yaw rows and G2.
    m = ac_data.EFF_MAT_G;
    rows = {'G1_thrust', 'Thrust'; 'G1_roll', 'Roll'; 'G1_pitch', 'Pitch'; 'G1_yaw', 'Yaw'; 'G2', 'G2'};
    rows = rows(isfield(m, rows(:, 1)), :);
    axs = pprz.tiles(size(rows, 1), 'Effectiveness');
    for k = 1:size(rows, 1)
        G = double(m.(rows{k, 1}));
        names = arrayfun(@(j) sprintf('Actuator %d', j), 1:size(G, 2), 'UniformOutput', false);
        pprz.series(axs(k), m.t, G, names);
        ylabel(axs(k), rows{k, 2});
    end
end
