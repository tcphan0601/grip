
#!/usr/bin/env python3                                                              
import os                                                                           
import sys
from argparse import ArgumentParser            
import numpy as np
                                                                                    
from core.bicrystal import Bicrystal                            
from core.simulation import Simulation
from utils.utils import make_dirs, get_inputs, make_crystals, compute_weights, \
                  get_xy_translation, get_xy_replications
                                                                                    
##############################################################################
                                                                                    
def main(infile: str, debug: bool) -> None:     
    """                                       
    Performs grand canonical optimization of GB structures.
                                                                                    
    Args:                             
        infile (str): YAML file of simulation parameters.
                                                                                    
        debug (bool): Flag for running in DEBUG mode.                
                                                                                    
    Returns:                               
        None.
    """                                         
                                                                                    
    # Read in parameters from YAML file.
    struct, algo = get_inputs(infile, debug)                                      

    # Create a Simulation object to orchestrate the simulation
    sim = Simulation(struct, algo, debug)                                               
    if debug: print(f"Starting GRIP calculations from {sim.root}")        
    if debug: print(f"This process is running in {sim.cfold}")                    
                                                                                                                                                                                                                                                                                
    # Create relevant directories like "best," "calc_proc#," etc
    make_dirs(sim.pid, algo["dir_struct"], algo["dir_calcs"])
                                                                                    
    if not os.path.exists(sim.summary_file):
        with open(sim.summary_file, "w") as f:
            f.write("Iteration,dx,dy,swapping,n_swaps,MD_run,MD_steps,Energy\n")

    # Use structure parameters to create upper and lower bulk slabs
    lower_0, upper_0, dlat = make_crystals(struct, debug)
                                                                                    
    # Compute the weights for replications
    weights = compute_weights(struct)
    if debug: print(f"The weight are: {weights}")
                                                                                    
    # Create a Bicrystal object from the two bulk slabs
    bicrystal = Bicrystal(lower_0, upper_0, struct, algo, dlat,
                          make_copy=False, debug=debug)
                                                                                    
    ##########################################################################

    # This loop samples different GB structures
    while sim.counter < sim.nruns or not sim.nruns:

        if debug: print(f"\n~~~~~ Starting simulation iteration {sim.counter+1} ~~~~~\n")

        # Make a copy of the parent slabs for each run
        bicrystal.copy_ul()

        # Sample a random translation and shift the upper slab
        dx, dy = get_xy_translation(upper_0, rng, algo["ngrid"], sim.pid, debug)
        bicrystal.shift_upper(dx, dy)
        if debug: print(f"Translation in (x, y) = [{dx:.4f}, {dy:.4f}]")

        # Get the bounds of the GB region for MD
        bicrystal.get_bounds(algo)
        if debug: print(f"Bounds for MD simulation (lower, upper, pad): \n{bicrystal.bounds}\n")

        # Sample a replication amount and replicate bicrystal in xy directions
        rx, ry = get_xy_replications(rng, weights)
        bicrystal.replicate(rx, ry)
        if debug: print(f"Replication in (x, y) = [{rx}, {ry}]")

        # Get the number of grain boundary atoms in the upper slab
        bicrystal.get_gbplane_atoms_u()
        if debug: print(f"Num atoms per plane in upper: {bicrystal.npp_u}\n")
        if debug: print(f"Num atoms in bicrystal: {bicrystal.natoms}")

        # Create vacancies in those grain boundary atoms in the upper slab
        bicrystal.defect_upper(algo, rng)

        if debug: print(f"{bicrystal} \n")
        if debug: print(f"Num atoms in bicrystal: {bicrystal.natoms}")
        if debug: print(f"n frac = {bicrystal.n}\n")

        # Perturb the GB atoms randomly
        bicrystal.perturb_atoms(rng)
        if debug: print(f"Perturbing GB atoms by {algo['perturb_u']} and {algo['perturb_l']}\n")

        # Combine the two slabs (upper w/ defects) into a single GB structure
        bicrystal.join_gb(algo)

        # Find interstitial sites and swap atoms in GB region
        swapped_n = bicrystal.find_and_swap_inters(rng)
        if debug: print(f"Swapping {swapped_n} GB atoms with interstitial sites.\n")

        # Write the GB structure to a file that is the initial structure for LAMMPS
        input_struct_file = os.path.join(algo["dir_calcs"], f"{algo['dir_calcs']}_{sim.pid+1}", "STRUC")
        bicrystal.write_gb(input_struct_file)

        if debug: print(bicrystal)
        if debug: print(f"GB structure is {bicrystal.gb}\n")

        # Sample parameters like Temperature and Numsteps for this iteration
        sim.sample_params(rng)
        if debug: print(f"The simulation parameters are T={sim.md_T}, N={sim.md_steps}")

        # Run the MD simulation to produce a final, relaxed GB structure
        if debug: print(f"Running MD sampling")
        sim.run_md(bicrystal, update_gb=True)

        # Get the GB energy from the last line in the final file (lammps_end_STRUC)
        sim.get_gb_energy(bicrystal)

        if debug: print(bicrystal)
        if debug: print(f"GB structure is {bicrystal.gb}\n")
        if debug: print(f"The GB energy is {bicrystal.Egb} J/m^2\n")

        # Store the energy in a list and save the file to the "best" folder
        sim.store_best_structs(bicrystal)

        sim.log_counter += 1

        # Identify if swapping occurred 
        # In this code, swapped_n is returned by bicrystal.find_and_swap_inters
        is_swapping = 1 if swapped_n > 0 else 0
        
        # Identify if an MD run occurred 
        # sim.md_steps is set to 0 in sim.sample_params if MD is skipped
        is_md = 1 if sim.md_steps > 0 else 0

        try:
            with open(sim.summary_file, "a") as f:
                # dx, dy are defined at 
                # sim.md_T, sim.md_steps are defined at [cite: 154, 156]
                # bicrystal.Egb is defined at [cite: 179]
                f.write(f"{sim.log_counter},{dx:.6f},{dy:.6f},"
                        f"{is_swapping},{swapped_n},"
                        f"{is_md},{sim.md_steps},"
                        f"{bicrystal.Egb:.8f}\n")
        except Exception as e:
            print(f"Rank {sim.pid+1} Log Error: {e}")
        if debug:# and sim.counter == 3:
            assert False, "Terminated early in DEBUG mode."

##############################################################################

if __name__ == "__main__":
    parser = ArgumentParser(description="Perform grand canonical optimization of GBs.")
    
    parser.add_argument("-i", "--input", type=str, required=True,
                        help="File containing structure & algorithm parameters (Required).")
    
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Run in DEBUG mode, which prints variables and terminates early.")
    parser.add_argument("-s", "--seed", type=int, default=1,
                        help="Random seed for debugging.")
    
    args = parser.parse_args()
    infile = args.input
    debug = args.debug
    seed = args.seed

    # SAFETY CHECK: Throw error if the file path does not exist
    if not os.path.exists(infile):
        print(f"\n[ERROR] The parameter file '{infile}' was not found.")
        print("Please check the path or ensure the directory is bind-mounted in Singularity.\n")
        sys.exit(1)

    if debug:
        rng = np.random.default_rng(seed=seed)
    else:
        rng = np.random.default_rng()

    main(infile, debug)

