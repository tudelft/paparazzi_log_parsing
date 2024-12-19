function cycl_plot_eff_mat(ac_data, order)

    Gpitch = double(string(ac_data.EFF_MAT_STAB.G1_pitch));
    Groll = double(string(ac_data.EFF_MAT_STAB.G1_roll));
    Gyaw = double(string(ac_data.EFF_MAT_STAB.G1_yaw));
    Gthrust = double(string(ac_data.EFF_MAT_STAB.G1_thrust));

    tiledlayout(2, 2, 'Padding', 'compact', 'TileSpacing', 'compact');

    ax1 = nexttile;
    hold on;
    h1 = plot(ac_data.EFF_MAT_STAB.timestamp, Gpitch(:,1));
    h2 = plot(ac_data.EFF_MAT_STAB.timestamp, Gpitch(:,2));
    h3 = plot(ac_data.EFF_MAT_STAB.timestamp, Gpitch(:,3));
    h4 = plot(ac_data.EFF_MAT_STAB.timestamp, Gpitch(:,4));
    xlabel('Time [s]');
    ylabel('');
    title('Gpitch');
    grid on;

    ax2 = nexttile;
    hold on;
    h5 = plot(ac_data.EFF_MAT_STAB.timestamp, Gyaw(:,1));
    h6 = plot(ac_data.EFF_MAT_STAB.timestamp, Gyaw(:,2));
    h7 = plot(ac_data.EFF_MAT_STAB.timestamp, Gyaw(:,3));
    h8 = plot(ac_data.EFF_MAT_STAB.timestamp, Gyaw(:,4));
    xlabel('Time [s]');
    ylabel('');
    title('Gyaw');
    grid on;

    ax3 = nexttile;
    hold on;
    h9 = plot(ac_data.EFF_MAT_STAB.timestamp, Groll(:,1));
    h10 = plot(ac_data.EFF_MAT_STAB.timestamp, Groll(:,2));
    h11 = plot(ac_data.EFF_MAT_STAB.timestamp, Groll(:,3));
    h12 = plot(ac_data.EFF_MAT_STAB.timestamp, Groll(:,4));
    xlabel('Time [s]');
    ylabel('');
    title('Groll');
    grid on;

    ax4 = nexttile;
    hold on;
    h13 = plot(ac_data.EFF_MAT_STAB.timestamp, Gthrust(:,1));
    h14 = plot(ac_data.EFF_MAT_STAB.timestamp, Gthrust(:,2));
    h15 = plot(ac_data.EFF_MAT_STAB.timestamp, Gthrust(:,3));
    h16 = plot(ac_data.EFF_MAT_STAB.timestamp, Gthrust(:,4));
    xlabel('Time [s]');
    ylabel('');
    title('Gthrust');
    grid on;

    linkaxes([ax1,ax2,ax3,ax4],'x');

    % background color fill for various flight modes
    mode_values = ac_data.ROTORCRAFT_RADIO_CONTROL.mode;
    mode_timestamps = ac_data.ROTORCRAFT_RADIO_CONTROL.timestamp;
    cycl_fill_mode_regions(mode_values, mode_timestamps, {ax1, ax2});
    legend(ax1, [h1,h2,h3,h4], {'Gpitch11', 'Gpitch12', 'Gpitch13', 'Gpitch14'});
    legend(ax2, [h5,h6,h7,h8], {'Gyaw11', 'Gyaw12', 'Gyaw13', 'Gyaw14'});
    legend(ax3, [h9,h10,h11,h12], {'Groll11', 'Groll12', 'Groll13', 'Groll14'});
    legend(ax4, [h13,h14,h15,h16], {'Gthrust11', 'Gthrust12', 'Gthrust13', 'Gthrust14'});
    
end