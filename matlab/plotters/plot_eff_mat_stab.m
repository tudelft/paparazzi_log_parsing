function plot_eff_mat_stab(ac_data)
%PLOT_EFF_MAT_STAB Stabilisation effectiveness per actuator: roll, pitch, yaw and thrust rows (the thrust
%   row from EFF_MAT_STAB_THRUST when logged), and G2.
    m = ac_data.EFF_MAT_STAB;
    rows = {'G1_roll', 'Roll'; 'G1_pitch', 'Pitch'; 'G1_yaw', 'Yaw'; 'G1_thrust', 'Thrust'; 'G2', 'G2'};
    src = repmat({m}, size(rows, 1), 1);
    if ~isfield(m, 'G1_thrust') && pprz.has(ac_data, 'EFF_MAT_STAB_THRUST')
        src{4} = ac_data.EFF_MAT_STAB_THRUST;
        rows{4, 1} = 'G1_z';
    end
    keep = cellfun(@(s, f) isfield(s, f), src, rows(:, 1));
    rows = rows(keep, :);
    src = src(keep);
    axs = pprz.tiles(size(rows, 1), 'Stabilisation effectiveness');
    for k = 1:size(rows, 1)
        G = double(src{k}.(rows{k, 1}));
        names = arrayfun(@(j) sprintf('Actuator %d', j), 1:size(G, 2), 'UniformOutput', false);
        pprz.series(axs(k), src{k}.t, G, names);
        ylabel(axs(k), rows{k, 2});
    end
end
