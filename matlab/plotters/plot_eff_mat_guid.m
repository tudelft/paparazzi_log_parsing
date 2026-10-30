function plot_eff_mat_guid(ac_data)
%PLOT_EFF_MAT_GUID Guidance effectiveness (EFF_MAT_GUID): effect of each guidance input on the north, east
%   and down acceleration.
    m = ac_data.EFF_MAT_GUID;
    rows = {'G1_aN', 'North'; 'G1_aE', 'East'; 'G1_aD', 'Down'};
    axs = pprz.tiles(3, 'Guidance effectiveness');
    for k = 1:3
        G = double(m.(rows{k, 1}));
        names = arrayfun(@(j) sprintf('Input %d', j), 1:size(G, 2), 'UniformOutput', false);
        pprz.series(axs(k), m.t, G, names);
        ylabel(axs(k), rows{k, 2});
    end
end
