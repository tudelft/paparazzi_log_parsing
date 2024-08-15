function plot_eff_mat(ac_data)
    
    
    if isfield(ac_data, 'EFF_MAT_G')
        G1thrust = double(string(ac_data.EFF_MAT_G.G1_thrust));
        G1pitch= double(string(ac_data.EFF_MAT_G.G1_pitch));
        G1roll = double(string(ac_data.EFF_MAT_G.G1_roll));
        G1yaw = double(string(ac_data.EFF_MAT_G.G1_yaw));
        G2 = double(string(ac_data.EFF_MAT_G.G2));

        ax1 = subplot(5,1,1);
        hold on
        plot(ac_data.EFF_MAT_G.timestamp, G1thrust)
        title('thrust')
        ax2 = subplot(5,1,2);
        plot(ac_data.EFF_MAT_G.timestamp, G1pitch)
        title('pitch')
        ax3 = subplot(5,1,3);
        plot(ac_data.EFF_MAT_G.timestamp, G1roll)
        title('roll')
        ax4 = subplot(5,1,4);
        plot(ac_data.EFF_MAT_G.timestamp, G1yaw)
        title('yaw')
        ax5 = subplot(5,1,5);
        plot(ac_data.EFF_MAT_G.timestamp, G2)
        xlabel('Time [s]');
        title('G2')

        sgtitle('Effectiveness')
        linkaxes([ax1, ax2, ax3, ax4, ax5],'x')
    end
end