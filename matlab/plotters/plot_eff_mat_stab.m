function plot_eff_mat_stab(ac_data, order)

    Gpitch = double(ac_data.EFF_MAT_STAB.G1_pitch);
    Groll = double(ac_data.EFF_MAT_STAB.G1_roll);
    Gyaw = double(ac_data.EFF_MAT_STAB.G1_yaw);
    Gthrust = double(ac_data.EFF_MAT_STAB.G1_thrust);

    tiledlayout(2, 2, 'Padding', 'compact', 'TileSpacing', 'compact');

    ax1 = nexttile;
    hold on;
    h1 = plot(ac_data.EFF_MAT_STAB.t, Gpitch(:,1), LineWidth=1.2);
    h2 = plot(ac_data.EFF_MAT_STAB.t, Gpitch(:,2), LineWidth=1.2);
    h3 = plot(ac_data.EFF_MAT_STAB.t, Gpitch(:,3), LineWidth=1.2);
    h4 = plot(ac_data.EFF_MAT_STAB.t, Gpitch(:,4), LineWidth=1.2);
    xlabel('Time [s]');
    ylabel('');
    title('Gpitch');
    grid on;

    ax2 = nexttile;
    hold on;
    h5 = plot(ac_data.EFF_MAT_STAB.t, Gyaw(:,1), LineWidth=1.2);
    h6 = plot(ac_data.EFF_MAT_STAB.t, Gyaw(:,2), LineWidth=1.2);
    h7 = plot(ac_data.EFF_MAT_STAB.t, Gyaw(:,3), LineWidth=1.2);
    h8 = plot(ac_data.EFF_MAT_STAB.t, Gyaw(:,4), LineWidth=1.2);
    xlabel('Time [s]');
    ylabel('');
    title('Gyaw');
    grid on;

    ax3 = nexttile;
    hold on;
    h9 = plot(ac_data.EFF_MAT_STAB.t, Groll(:,1), LineWidth=1.2);
    h10 = plot(ac_data.EFF_MAT_STAB.t, Groll(:,2), LineWidth=1.2);
    h11 = plot(ac_data.EFF_MAT_STAB.t, Groll(:,3), LineWidth=1.2);
    h12 = plot(ac_data.EFF_MAT_STAB.t, Groll(:,4), LineWidth=1.2);
    xlabel('Time [s]');
    ylabel('');
    title('Groll');
    grid on;

    ax4 = nexttile;
    hold on;
    h13 = plot(ac_data.EFF_MAT_STAB.t, Gthrust(:,1), LineWidth=1.2);
    h14 = plot(ac_data.EFF_MAT_STAB.t, Gthrust(:,2), LineWidth=1.2);
    h15 = plot(ac_data.EFF_MAT_STAB.t, Gthrust(:,3), LineWidth=1.2);
    h16 = plot(ac_data.EFF_MAT_STAB.t, Gthrust(:,4), LineWidth=1.2);
    xlabel('Time [s]');
    ylabel('');
    title('Gthrust');
    grid on;

    legend(ax1, [h1,h2,h3,h4], {'Gpitch11', 'Gpitch12', 'Gpitch13', 'Gpitch14'});
    legend(ax2, [h5,h6,h7,h8], {'Gyaw11', 'Gyaw12', 'Gyaw13', 'Gyaw14'});
    legend(ax3, [h9,h10,h11,h12], {'Groll11', 'Groll12', 'Groll13', 'Groll14'});
    legend(ax4, [h13,h14,h15,h16], {'Gthrust11', 'Gthrust12', 'Gthrust13', 'Gthrust14'});

    linkaxes([ax1,ax2,ax3,ax4],'x');

end